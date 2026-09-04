"""Pipeline de automação (login, pesquisa, exportação e upload dos
relatórios pro S3) — separado de main.py para que este último possa ser só
o ponto de entrada da aplicação (sobe o servidor web), sem risco de
import circular com web.py."""

import logging
import threading
from typing import Callable, Optional

from playwright.sync_api import sync_playwright

import modules.history as history
import modules.storage as storage
from modules.browser import SessaoElaw
from modules.cancel import checar_cancelamento
from modules.elaw import PAUTA_GERAL_NOME, REPORT_NAMES
from modules.login import LoginConfig
from modules.progress import OnReport, OnStep, emit_report
from pesquisar import pesquisar

logger = logging.getLogger(__name__)


def executar(
    on_step: OnStep = None,
    on_report: OnReport = None,
    cancel_event: Optional[threading.Event] = None,
    on_sessao: Optional[Callable[[SessaoElaw], None]] = None,
    run_id: Optional[int] = None,
    usuario: Optional[str] = None,
) -> dict[str, str]:
    """Executa o pipeline completo (login, pesquisa, exportação e upload
    dos relatórios pro S3) e retorna {nome_relatorio: chave_s3}.
    Relatórios já registrados hoje no elaw (ex.: execução anterior
    interrompida) são detectados automaticamente e reaproveitados, sem
    reexportar. Relatórios já enviados hoje pro S3 também são reaproveitados,
    sem reexportar nem reenviar.

    `on_step`/`on_report`, se informados, recebem atualizações de progresso
    — usados pela interface para acompanhar a execução em tempo real.

    `cancel_event`, se informado, é checado entre as etapas do pipeline;
    quando marcado, a execução é interrompida com `ExecucaoCancelada`.
    `on_sessao`, se informado, recebe a `SessaoElaw` assim que o login é
    concluído — permite que quem chamou force o cancelamento (fechando o
    navegador) mesmo durante uma espera longa em andamento."""
    if run_id is None:
        run_id = history.create_run_record(usuario=usuario)

    checar_cancelamento(cancel_event)
    pasta = storage.pasta_do_dia()
    todos_nomes = REPORT_NAMES + [PAUTA_GERAL_NOME]
    existentes = storage.relatorios_existentes(pasta)

    if len(existentes) == len(todos_nomes):
        logger.info(
            f"Todos os relatórios do dia já existem no S3 (pasta {pasta}), navegador não será aberto.")
        for nome in todos_nomes:
            emit_report(on_report, nome, "downloaded", existentes[nome])
        return existentes

    for nome, chave in existentes.items():
        logger.info(
            f"Relatório '{nome}' já existe no S3 ({chave}), pulando geração.")
        emit_report(on_report, nome, "downloaded", chave)

    config = LoginConfig.from_env()

    with sync_playwright() as p:
        sessao = SessaoElaw(p, config, on_step)
        if on_sessao:
            on_sessao(sessao)
        try:
            return pesquisar(
                sessao.page, sessao.recover, pasta, existentes,
                on_step=on_step, on_report=on_report, cancel_event=cancel_event,
                run_id=run_id,
            )
        finally:
            sessao.close()
