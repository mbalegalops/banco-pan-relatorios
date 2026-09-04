"""Orquestra a pesquisa de processos no elaw e a exportação/download dos
relatórios "DADOS DO PROCESSO", "ESCRITÓRIO - TAREFAS" e "PAUTA GERAL"."""

import logging
import threading
from typing import Callable, Optional

from playwright.sync_api import Page

import modules.history as history
from modules.cancel import checar_cancelamento
from modules.download import aguardar_e_baixar_relatorios, detectar_relatorios_em_processamento
from modules.elaw import (
    PAUTA_GERAL_NOME,
    REPORT_NAMES,
    SEARCH_URL,
    exportar_para_excel,
    exportar_pauta_geral,
    executar_pesquisa,
    selecionar_filtros,
)
from modules.progress import OnReport, OnStep, emit_report, emit_step

logger = logging.getLogger(__name__)


def pesquisar(
    page: Page,
    recover: Callable[[], Page],
    pasta: str,
    existentes: dict[str, str],
    on_step: OnStep = None,
    on_report: OnReport = None,
    cancel_event: Optional[threading.Event] = None,
    run_id: Optional[int] = None,
) -> dict[str, str]:
    """Aplica os filtros de status, pesquisa, exporta os relatórios
    "DADOS DO PROCESSO", "ESCRITÓRIO - TAREFAS" e "PAUTA GERAL", aguarda
    todos ficarem prontos e envia todos pro S3, na pasta `pasta` (AAAA-MM-DD).

    `existentes` traz os relatórios do dia já gerados anteriormente
    (nome -> chave S3), verificados antes de abrir o navegador; apenas os
    relatórios ausentes desse dict são exportados/enviados aqui.

    Antes de exportar, a página de relatórios do elaw é verificada em busca
    de relatórios já registrados hoje (ex.: execução anterior interrompida
    antes do download); esses são reaproveitados em vez de reexportados.

    `recover` fecha o navegador atual, abre um novo, refaz o login e
    retorna a nova página — usado caso a sessão caia durante a espera
    do processamento dos relatórios.

    `on_step` e `on_report`, se informados, são chamados para reportar o
    progresso do pipeline e o status de cada relatório à interface.

    `cancel_event`, se informado, é checado entre as etapas — quando
    marcado, a execução é interrompida com `ExecucaoCancelada`."""
    checar_cancelamento(cancel_event)
    todos_nomes = REPORT_NAMES + [PAUTA_GERAL_NOME]
    nomes_pendentes = [nome for nome in todos_nomes if nome not in existentes]

    relatorio_ids: dict[str, str] = {}
    if nomes_pendentes:
        emit_step(on_step, "verificacao", "running")
        relatorio_ids = detectar_relatorios_em_processamento(page, nomes_pendentes)
        emit_step(on_step, "verificacao", "done")
        for nome in relatorio_ids:
            emit_report(on_report, nome, "waiting")

    checar_cancelamento(cancel_event)
    pendentes_geral = [nome for nome in REPORT_NAMES if nome not in existentes and nome not in relatorio_ids]
    pauta_pendente = PAUTA_GERAL_NOME not in existentes and PAUTA_GERAL_NOME not in relatorio_ids

    if pendentes_geral:
        logger.info("Navegando para a página de pesquisa...")
        page.goto(SEARCH_URL, wait_until="domcontentloaded")

        emit_step(on_step, "filtros", "running")
        selecionar_filtros(page)
        emit_step(on_step, "filtros", "done")

        emit_step(on_step, "pesquisa", "running")
        executar_pesquisa(page)
        emit_step(on_step, "pesquisa", "done")
    else:
        logger.info(
            "Nenhum relatório precisa de nova pesquisa (todos já existentes ou em processamento) — "
            "pulando seleção de filtros e execução da pesquisa.")
        emit_step(on_step, "filtros", "skipped")
        emit_step(on_step, "pesquisa", "skipped")

    emit_step(on_step, "exportacao", "running")
    falhas_exportacao = []
    for nome in pendentes_geral:
        checar_cancelamento(cancel_event)
        try:
            emit_report(on_report, nome, "waiting")
            relatorio_ids[nome] = exportar_para_excel(page, nome)
        except Exception as exc:
            logger.error(f"Erro ao exportar '{nome}': {exc}")
            emit_report(on_report, nome, "error")
            falhas_exportacao.append(nome)
    if pauta_pendente:
        checar_cancelamento(cancel_event)
        try:
            emit_report(on_report, PAUTA_GERAL_NOME, "waiting")
            relatorio_ids[PAUTA_GERAL_NOME] = exportar_pauta_geral(page)
        except Exception as exc:
            logger.error(f"Erro ao exportar '{PAUTA_GERAL_NOME}': {exc}")
            emit_report(on_report, PAUTA_GERAL_NOME, "error")
            falhas_exportacao.append(PAUTA_GERAL_NOME)
    emit_step(on_step, "exportacao", "done")

    checar_cancelamento(cancel_event)
    emit_step(on_step, "aguardar", "running")
    emit_step(on_step, "download", "running")
    baixados, falhados_download, page = aguardar_e_baixar_relatorios(
        page, relatorio_ids, recover, pasta, on_report, cancel_event=cancel_event)
    emit_step(on_step, "aguardar", "done")
    emit_step(on_step, "download", "done")

    if run_id is not None:
        for nome_rel, id_rel in falhados_download.items():
            history.registrar_relatorio_falhado(run_id, nome_rel, id_rel)

    return {**existentes, **baixados}
