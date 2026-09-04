"""Persistência do histórico de execuções em MongoDB (remoto — permite que
qualquer máquina rodando o app veja o status e os relatórios de todas as
outras, ao contrário do antigo runs.db em SQLite, que era local por máquina).

Mantém a mesma interface pública que a versão SQLite (list_runs,
create_run_record, finish_run_record, get_run) para não exigir mudanças
no restante do app (web.py)."""

import getpass
from datetime import datetime
from typing import Optional

from dotenv import dotenv_values
from pymongo import MongoClient, ReturnDocument

from modules.paths import APP_DIR

ENV_FILE = APP_DIR / ".env"

_client: Optional[MongoClient] = None
_db = None


def _database():
    global _client, _db
    if _db is None:
        mongo_uri = dotenv_values(ENV_FILE).get("MONGO_URI")
        if not mongo_uri:
            raise RuntimeError(
                "MONGO_URI não configurada no .env — necessária para o histórico de execuções.")
        _client = MongoClient(mongo_uri)
        _db = _client.get_default_database()
    return _db


def init_db() -> None:
    db = _database()
    db.runs.create_index("status")


def _proximo_id(db) -> int:
    """Contador atômico para manter ids inteiros sequenciais (iguais aos do
    antigo SQLite AUTOINCREMENT), em vez de ObjectId — a GUI usa o id como
    chave de seleção/ordenação (ex.: `Execução #{id}`)."""
    doc = db.counters.find_one_and_update(
        {"_id": "runs"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


def create_run_record() -> int:
    db = _database()
    run_id = _proximo_id(db)
    db.runs.insert_one({
        "_id": run_id,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "status": "running",
        "downloads": {},
        "error": None,
        "usuario": getpass.getuser(),
        "log_uri": None,
    })
    return run_id


def registrar_download(run_id: int, nome_relatorio: str, chave_s3: str) -> None:
    """Grava a chave S3 (URI) de um relatório assim que ele fica pronto —
    chamado a cada relatório durante a execução (não só no fim, como
    `finish_run_record`), para que o botão de download apareça na hora em
    que a execução ainda está rodando, sem esperar ela terminar.

    `downloads` é um dict {nome_relatorio: chave_s3_completa} — grava a
    chave inteira (não só nome do arquivo + pasta separados), pra a URI
    real do relatório ficar explícita no banco."""
    db = _database()
    db.runs.update_one(
        {"_id": run_id},
        {"$set": {f"downloads.{nome_relatorio}": chave_s3}},
    )


def finish_run_record(
    run_id: int,
    error: Optional[str],
    downloads: dict[str, str],
    cancelado: bool = False,
) -> None:
    status = "cancelled" if cancelado else ("error" if error else "success")
    db = _database()
    db.runs.update_one(
        {"_id": run_id},
        {"$set": {
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "downloads": downloads,
            "error": error,
        }},
    )


def registrar_log(run_id: int, chave_s3: str) -> None:
    """Grava a chave S3 (URI) do log de uma execução, assim que ele é
    enviado ao fim dela — a GUI busca o log ali (`obter_texto`), nunca do
    disco local da máquina que rodou."""
    db = _database()
    db.runs.update_one(
        {"_id": run_id},
        {"$set": {"log_uri": chave_s3}},
    )


def registrar_relatorio_falhado(run_id: int, nome_relatorio: str, relatorio_id: str) -> None:
    """Registra um relatório que falhou no download, armazenando seu ID para
    possibilitar retry posterior."""
    db = _database()
    db.runs.update_one(
        {"_id": run_id},
        {"$set": {f"failed_reports.{nome_relatorio}": relatorio_id}},
        upsert=False,
    )


def remover_relatorio_falhado(run_id: int, nome_relatorio: str) -> None:
    """Remove um relatório da lista de falhados quando retry é bem-sucedido."""
    db = _database()
    db.runs.update_one(
        {"_id": run_id},
        {"$unset": {f"failed_reports.{nome_relatorio}": 1}},
    )


def _com_id(doc: dict) -> dict:
    run = dict(doc)
    run["id"] = run.pop("_id")
    return run


def list_runs(limit: int = 200) -> list[dict]:
    db = _database()
    cursor = db.runs.find().sort("_id", -1).limit(limit)
    return [_com_id(doc) for doc in cursor]


def get_run(run_id: int) -> Optional[dict]:
    db = _database()
    doc = db.runs.find_one({"_id": run_id})
    return _com_id(doc) if doc else None


# ===================================================================== Agendas


def create_schedule(
    hour: int,
    minute: int,
    days: list[int],
    enabled: bool = True,
) -> str:
    """Cria novo agendamento e retorna seu ID.

    Args:
        hour: Hora (0-23)
        minute: Minuto (0-59)
        days: Lista de dias da semana (0=seg, 1=ter, ..., 6=dom)
        enabled: Se agenda está ativa

    Returns:
        ID da agenda criada (como string)
    """
    db = _database()
    from bson.objectid import ObjectId

    # Converte dias para expressão cron (0=seg..6=dom em cron, onde 0=dom..6=sab)
    cron_days = ",".join(str((d + 1) % 7) for d in days) if days else "*"
    cron_expression = f"{minute} {hour} * * {cron_days}"

    schedule = {
        "_id": ObjectId(),
        "hour": hour,
        "minute": minute,
        "days": days,
        "cron_expression": cron_expression,
        "enabled": enabled,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": getpass.getuser(),
        "last_run_at": None,
        "next_run_at": None,
        "last_status": None,
        "last_run_id": None,
    }
    result = db.schedules.insert_one(schedule)
    return str(result.inserted_id)


def update_schedule(
    schedule_id: str,
    hour: Optional[int] = None,
    minute: Optional[int] = None,
    days: Optional[list[int]] = None,
    enabled: Optional[bool] = None,
) -> None:
    """Atualiza agendamento existente.

    Args:
        schedule_id: ID da agenda (como string)
        hour: Nova hora (None = manter atual)
        minute: Novo minuto (None = manter atual)
        days: Novos dias da semana (None = manter atual)
        enabled: Nova status
    """
    db = _database()
    from bson.objectid import ObjectId

    # Valida que a agenda existe
    try:
        doc = db.schedules.find_one({"_id": ObjectId(schedule_id)})
        if not doc:
            raise ValueError(f"Agenda '{schedule_id}' não encontrada")
    except Exception as exc:
        if "not a valid ObjectId" in str(exc):
            raise ValueError(f"ID de agenda inválido: {schedule_id}")
        raise exc

    updates = {}
    if hour is not None:
        updates["hour"] = hour
    if minute is not None:
        updates["minute"] = minute
    if days is not None:
        updates["days"] = days
        # Regenera expressão cron com valores atuais ou novos
        new_hour = hour if hour is not None else doc.get("hour", 0)
        new_minute = minute if minute is not None else doc.get("minute", 0)
        cron_days = ",".join(str((d + 1) % 7) for d in days) if days else "*"
        updates["cron_expression"] = f"{new_minute} {new_hour} * * {cron_days}"
    if enabled is not None:
        updates["enabled"] = enabled

    if updates:
        db.schedules.update_one({"_id": ObjectId(schedule_id)}, {"$set": updates})


def delete_schedule(schedule_id: str) -> None:
    """Deleta agendamento.

    Args:
        schedule_id: ID da agenda
    """
    db = _database()
    from bson.objectid import ObjectId

    db.schedules.delete_one({"_id": ObjectId(schedule_id)})


def list_schedules() -> list[dict]:
    """Retorna todas as agendas."""
    db = _database()
    cursor = db.schedules.find().sort("_id", -1)
    schedules = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        schedules.append(doc)
    return schedules


def get_schedule(schedule_id: str) -> Optional[dict]:
    """Retorna uma agenda específica.

    Args:
        schedule_id: ID da agenda

    Returns:
        Dict com dados da agenda ou None
    """
    db = _database()
    from bson.objectid import ObjectId

    doc = db.schedules.find_one({"_id": ObjectId(schedule_id)})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def update_schedule_after_run(
    schedule_id: str,
    last_run_at: str,
    last_status: str,
    last_run_id: Optional[int] = None,
) -> None:
    """Atualiza histórico de execução de uma agenda.

    Args:
        schedule_id: ID da agenda
        last_run_at: Data/hora da última execução (ISO format)
        last_status: Status da última execução (success/error)
        last_run_id: ID do run gerado (opcional)
    """
    db = _database()
    from bson.objectid import ObjectId

    updates = {
        "last_run_at": last_run_at,
        "last_status": last_status,
    }
    if last_run_id is not None:
        updates["last_run_id"] = last_run_id

    db.schedules.update_one({"_id": ObjectId(schedule_id)}, {"$set": updates})
