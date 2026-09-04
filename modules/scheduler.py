"""Gerenciador de agendamentos com APScheduler e MongoDB."""

import logging
from datetime import datetime
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from pytz import timezone
from pymongo import MongoClient

import modules.history as history

logger = logging.getLogger(__name__)

TZ_BRASIL = timezone("America/Manaus")  # GMT-4 (Amazonas)


class Scheduler:
    """Gerencia agendamentos de execução de relatórios usando APScheduler."""

    def __init__(self, mongo_uri: str):
        """
        Args:
            mongo_uri: URI do MongoDB (ex: mongodb://root:pass@host:27018/dbname)
        """
        self.mongo_uri = mongo_uri
        self.scheduler: Optional[BackgroundScheduler] = None

    def start(
        self,
        on_job_executed: Optional[Callable[[int, str], None]] = None,
        on_job_error: Optional[Callable[[str, Exception], None]] = None,
    ) -> None:
        """Inicia o scheduler e carrega agendas do MongoDB.

        Args:
            on_job_executed: Callback chamado quando job termina (schedule_id, run_id)
            on_job_error: Callback chamado quando job falha (schedule_id, exception)
        """
        if self.scheduler is not None:
            logger.warning("Scheduler já está em execução")
            return

        from apscheduler.jobstores.memory import MemoryJobStore

        jobstores = {
            "default": MemoryJobStore(),
        }
        executors = {"default": ThreadPoolExecutor(max_workers=2)}
        job_defaults = {"coalesce": True, "max_instances": 1}

        self.scheduler = BackgroundScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone=TZ_BRASIL,
        )

        self.on_job_executed = on_job_executed
        self.on_job_error = on_job_error

        self.scheduler.start()
        logger.info("Scheduler iniciado")

        self._load_schedules()

    def stop(self) -> None:
        """Para o scheduler."""
        if self.scheduler is not None:
            self.scheduler.shutdown()
            self.scheduler = None
            logger.info("Scheduler parado")

    def _load_schedules(self) -> None:
        """Carrega agendas do MongoDB e as registra no APScheduler."""
        if self.scheduler is None:
            return

        logger.info("Carregando agendas do MongoDB...")
        schedules = history.list_schedules()

        for schedule in schedules:
            if not schedule.get("enabled", False):
                continue

            schedule_id = str(schedule.get("_id", ""))
            cron_expr = schedule.get("cron_expression", "0 8 * * *")

            try:
                self.scheduler.add_job(
                    self._execute_schedule,
                    trigger="cron",
                    args=[schedule_id],
                    id=schedule_id,
                    name=schedule.get("name", "Sem nome"),
                    replace_existing=True,
                    **self._parse_cron(cron_expr),
                )
                logger.info(f"Agenda '{schedule_id}' carregada: {cron_expr}")
            except Exception as exc:
                logger.error(f"Erro ao carregar agenda '{schedule_id}': {exc}")

    def add_schedule(
        self,
        schedule_id: str,
        name: str,
        cron_expression: str,
        enabled: bool = True,
    ) -> None:
        """Adiciona nova agenda ao scheduler.

        Args:
            schedule_id: ID único da agenda (ObjectId como string)
            name: Nome da agenda
            cron_expression: Expressão cron (ex: "0 8 * * *")
            enabled: Se agenda está ativa
        """
        if self.scheduler is None:
            logger.warning("Scheduler não iniciado")
            return

        if not enabled:
            self.remove_schedule(schedule_id)
            return

        try:
            self.scheduler.add_job(
                self._execute_schedule,
                trigger="cron",
                args=[schedule_id],
                id=schedule_id,
                name=name,
                replace_existing=True,
                **self._parse_cron(cron_expression),
            )
            logger.info(f"Agenda '{schedule_id}' adicionada ao scheduler")
        except Exception as exc:
            logger.error(f"Erro ao adicionar agenda '{schedule_id}': {exc}")

    def remove_schedule(self, schedule_id: str) -> None:
        """Remove agenda do scheduler.

        Args:
            schedule_id: ID da agenda a remover
        """
        if self.scheduler is None:
            return

        try:
            self.scheduler.remove_job(schedule_id)
            logger.info(f"Agenda '{schedule_id}' removida do scheduler")
        except Exception as exc:
            logger.error(f"Erro ao remover agenda '{schedule_id}': {exc}")

    def update_schedule(
        self,
        schedule_id: str,
        cron_expression: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        """Atualiza agenda existente.

        Args:
            schedule_id: ID da agenda
            cron_expression: Nova expressão cron (None = manter atual)
            enabled: Nova status de ativação (None = manter atual)
        """
        schedule = history.get_schedule(schedule_id)
        if not schedule:
            logger.warning(f"Agenda '{schedule_id}' não encontrada")
            return

        if cron_expression is None:
            cron_expression = schedule.get("cron_expression", "0 8 * * *")
        if enabled is None:
            enabled = schedule.get("enabled", False)

        self.remove_schedule(schedule_id)

        if enabled:
            self.add_schedule(
                schedule_id,
                schedule.get("name", "Sem nome"),
                cron_expression,
                enabled,
            )

    def list_jobs(self) -> list[dict]:
        """Retorna lista de jobs agendados."""
        if self.scheduler is None:
            return []

        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                }
            )
        return jobs

    def _execute_schedule(self, schedule_id: str) -> None:
        """Executa uma agenda (callback do APScheduler).

        Args:
            schedule_id: ID da agenda a executar
        """
        logger.info(f"Executando agenda '{schedule_id}'...")

        from pipeline import executar

        try:
            downloads = executar(run_id=None)

            history.update_schedule_after_run(
                schedule_id,
                last_run_at=datetime.now().isoformat(timespec="seconds"),
                last_status="success",
            )

            if self.on_job_executed:
                self.on_job_executed(schedule_id, None)

            logger.info(f"Agenda '{schedule_id}' executada com sucesso")
        except Exception as exc:
            logger.exception(f"Erro ao executar agenda '{schedule_id}'")

            history.update_schedule_after_run(
                schedule_id,
                last_run_at=datetime.now().isoformat(timespec="seconds"),
                last_status="error",
            )

            if self.on_job_error:
                self.on_job_error(schedule_id, exc)

    @staticmethod
    def _parse_cron(cron_expression: str) -> dict:
        """Converte expressão cron para parâmetros APScheduler.

        Args:
            cron_expression: Ex: "0 8 * * *"

        Returns:
            Dict com parâmetros para APScheduler (hour, minute, etc)
        """
        parts = cron_expression.split()
        if len(parts) != 5:
            raise ValueError(f"Expressão cron inválida: {cron_expression}")

        minute, hour, day, month, day_of_week = parts

        return {
            "minute": minute,
            "hour": hour,
            "day": day,
            "month": month,
            "day_of_week": day_of_week,
        }
