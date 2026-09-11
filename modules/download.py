"""Fluxo de polling e download dos relatórios exportados no elaw."""

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page

import modules.sharepoint as sharepoint
import modules.storage as storage
from modules.cancel import checar_cancelamento
from modules.elaw import REPORT_FILE_PREFIXES
from modules.paths import TEMP_DOWNLOADS_DIR
from modules.progress import OnReport, emit_report
from modules.report_filter import filtrar_processos_mascarenhas

logger = logging.getLogger(__name__)

REPORT_LIST_URL = "https://bancopan.elaw.com.br/userProcessoReportTmpList.elaw?faces-redirect=true"

POLL_TIMEOUT = 10800  # 3 horas
POLL_INTERVAL_MS = 60000
POLL_MAX_ATTEMPTS = 3
POLL_RETRY_DELAY = 2.0


def _clip_selector(relatorio_id: str) -> str:
    row_selector = f"//tbody[@id='tableProcessoReportTmp_data']/tr[td[3][text()='{relatorio_id}']]"
    return f"{row_selector}//img[contains(@title, 'pronto para download') or contains(@src, 'clip')]"


def detectar_relatorios_em_processamento(page: Page, nomes: list[str]) -> dict[str, str]:
    """Verifica na página de relatórios do elaw se algum dos `nomes` já foi
    registrado hoje (ex.: execução anterior interrompida antes do download)
    e retorna {nome: id} para reaproveitar — evita reexportar o relatório e
    substitui a necessidade de informar o ID manualmente.

    Quando há mais de um registro do mesmo modelo hoje, considera sempre o
    mais antigo (o primeiro a ter sido solicitado), não o mais recente —
    é esse que corresponde a uma execução anterior interrompida."""
    logger.info("Verificando relatórios já registrados hoje na lista do elaw...")
    try:
        page.goto(REPORT_LIST_URL, wait_until="domcontentloaded")
        page.locator('//*[@id="btnPesquisar"]').click()
        page.wait_for_selector("#tableProcessoReportTmp_data", timeout=10000)
    except Exception as exc:
        logger.warning(f"Não foi possível verificar relatórios já registrados: {exc}")
        return {}

    hoje = time.strftime("%d/%m/%Y")
    linhas = page.locator("//tbody[@id='tableProcessoReportTmp_data']/tr")

    candidatos: dict[str, tuple[datetime, str]] = {}
    for i in range(linhas.count()):
        linha = linhas.nth(i)
        # `all_text_contents()` lê as células como estão agora, sem esperar
        # nenhuma ficar "estável" - diferente de `.text_content()` num
        # `td[N]` específico, que fica retentando até dar timeout se aquela
        # posição não existir. Isso importa porque, quando a pesquisa não
        # retorna nenhum relatório, o eLaw renderiza uma única linha "Nenhum
        # registro encontrado" com uma célula só (colspan) em vez das 12
        # colunas normais - sem essa checagem de tamanho, cair nessa linha
        # travava 30s esperando por uma 9ª coluna que nunca ia aparecer.
        celulas = linha.locator("td").all_text_contents()
        if len(celulas) < 10:
            continue

        modelo = celulas[8].strip()
        if modelo not in nomes:
            continue

        data_registrado_texto = celulas[9].strip()
        if not data_registrado_texto.startswith(hoje):
            continue
        try:
            data_registrado = datetime.strptime(data_registrado_texto, "%d/%m/%Y %H:%M")
        except ValueError:
            continue

        relatorio_id = celulas[2].strip()
        if not relatorio_id:
            continue

        mais_antigo = candidatos.get(modelo)
        if mais_antigo is None or data_registrado < mais_antigo[0]:
            candidatos[modelo] = (data_registrado, relatorio_id)

    encontrados = {modelo: relatorio_id for modelo, (_, relatorio_id) in candidatos.items()}
    for modelo, relatorio_id in encontrados.items():
        logger.info(
            f"Relatório '{modelo}' já registrado hoje (ID {relatorio_id}), reaproveitando em vez de reexportar.")

    return encontrados


def _baixar_relatorio(page: Page, nome_relatorio: str, clip_selector: str, pasta: str) -> str:
    """Baixa o relatório do elaw, envia para o S3 (pasta do dia) e retorna
    a chave S3. O Playwright só sabe salvar em disco local — o arquivo
    passa por um temporário (`TEMP_DOWNLOADS_DIR`) que é apagado assim que
    o upload termina; o S3 é a fonte da verdade, não essa pasta."""
    logger.info(f"Baixando relatório '{nome_relatorio}'...")
    with page.expect_download() as download_info:
        page.locator(clip_selector).click()
    download = download_info.value

    sugerido = download.suggested_filename or ""
    extensao = Path(sugerido).suffix or ".xlsx"
    prefixo = REPORT_FILE_PREFIXES.get(nome_relatorio, nome_relatorio.replace(" ", "_"))
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{prefixo}_{timestamp}{extensao}"

    TEMP_DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    caminho_temp = TEMP_DOWNLOADS_DIR / filename
    download.save_as(str(caminho_temp))

    # S3, SharePoint e a interface sempre recebem a mesma versão saneada.
    # O filtro é um no-op para DADOS DO PROCESSO.
    filtrar_processos_mascarenhas(caminho_temp, nome_relatorio)

    chave_s3 = storage.enviar_relatorio(caminho_temp, pasta)

    # A cópia para o SharePoint é feita em uma thread. O temporário só pode
    # ser removido quando essa thread terminar; removê-lo aqui fazia a thread
    # eventualmente tentar copiar um arquivo que já não existia.
    def remover_temporario_sharepoint(_sucesso: bool, _mensagem: str) -> None:
        caminho_temp.unlink(missing_ok=True)

    sharepoint.enviar_relatorio_sharepoint(
        caminho_temp,
        pasta,
        on_complete=remover_temporario_sharepoint,
    )
    logger.info(f"Relatório '{nome_relatorio}' salvo em s3://{storage.S3_BUCKET}/{chave_s3}")
    return chave_s3


def aguardar_e_baixar_relatorios(
    page: Page,
    relatorio_ids: dict[str, str],
    recover: Callable[[], Page],
    pasta: str,
    on_report: OnReport = None,
    cancel_event: threading.Event | None = None,
) -> tuple[dict[str, str], dict[str, str], Page]:
    """Faz polling e baixa cada relatório assim que ele ficar pronto, sem
    esperar os demais. Retorna um dict {nome_relatorio: chave_s3}, um dict
    com os relatórios que falharam {nome: id}, e a página (possivelmente
    recriada) em uso."""
    logger.info("Navegando para a lista de relatórios...")
    page.goto(REPORT_LIST_URL, wait_until="domcontentloaded")

    pendentes = dict(relatorio_ids)
    baixados: dict[str, str] = {}
    falhados_download: dict[str, str] = {}  # {nome: erro_mensagem}
    page_ref = [page]

    start_time = time.monotonic()
    while pendentes and time.monotonic() - start_time < POLL_TIMEOUT:
        checar_cancelamento(cancel_event)
        for tentativa in range(1, POLL_MAX_ATTEMPTS + 1):
            try:
                page_ref[0].locator('//*[@id="btnPesquisar"]').click()
                page_ref[0].wait_for_selector("#tableProcessoReportTmp_data", timeout=10000)
                break
            except Exception as exc:
                if tentativa == POLL_MAX_ATTEMPTS:
                    logger.warning(
                        f"Erro persistente ao atualizar a lista de relatórios: {exc}. "
                        "Reabrindo navegador, refazendo login e voltando à página de relatórios..."
                    )
                    page_ref[0] = recover()
                    page_ref[0].goto(REPORT_LIST_URL, wait_until="domcontentloaded")
                else:
                    logger.warning(f"Erro temporário ao atualizar a lista de relatórios ({tentativa}/{POLL_MAX_ATTEMPTS}): {exc}")
                    time.sleep(POLL_RETRY_DELAY)

        for nome, relatorio_id in list(pendentes.items()):
            clip = _clip_selector(relatorio_id)
            if page_ref[0].locator(clip).count() > 0:
                logger.info(f"Relatório '{nome}' (ID {relatorio_id}) pronto para download.")
                emit_report(on_report, nome, "ready")
                try:
                    chave_s3 = _baixar_relatorio(page_ref[0], nome, clip, pasta)
                    baixados[nome] = chave_s3
                    emit_report(on_report, nome, "downloaded", chave_s3)
                except Exception as exc:
                    logger.error(f"Erro ao baixar relatório '{nome}': {exc}")
                    falhados_download[nome] = str(exc)
                    emit_report(on_report, nome, "error")
                del pendentes[nome]
            else:
                logger.info(f"Relatório '{nome}' (ID {relatorio_id}) ainda em processamento.")

        if pendentes:
            page_ref[0].wait_for_timeout(POLL_INTERVAL_MS)

    # Relatórios que não ficaram prontos no tempo limite (timeout)
    if pendentes:
        faltantes = ", ".join(f"{nome} (ID {rid})" for nome, rid in pendentes.items())
        logger.error(f"Relatório(s) não terminaram de processar em até {POLL_TIMEOUT}s: {faltantes}")
        for nome in pendentes:
            emit_report(on_report, nome, "error")
            falhados_download[nome] = f"Timeout após {POLL_TIMEOUT}s"

    if falhados_download:
        logger.warning(f"Relatórios com erro: {', '.join(falhados_download.keys())}")

    # Retorna também os relatórios que falharam (com seus IDs do elaw)
    # para possibilitar retry posterior
    falhados_com_id = {nome: relatorio_ids[nome] for nome in falhados_download.keys() if nome in relatorio_ids}

    return baixados, falhados_com_id, page_ref[0]


def fazer_download_relatorio_individual(
    page: Page,
    nome_relatorio: str,
    relatorio_id: str,
    pasta: str,
    recover: Callable[[], Page],
    on_report: OnReport = None,
) -> str | None:
    """Tenta fazer download de um único relatório que falhou anteriormente.

    Retorna a chave S3 se sucesso, ou None se falhar."""
    logger.info(f"Iniciando retry de download para '{nome_relatorio}' (ID {relatorio_id})...")
    emit_report(on_report, nome_relatorio, "retrying")

    page_ref = [page]
    start_time = time.monotonic()

    while time.monotonic() - start_time < POLL_TIMEOUT:
        try:
            page_ref[0].goto(REPORT_LIST_URL, wait_until="domcontentloaded")
        except Exception as exc:
            logger.warning(f"Erro ao navegar para página de relatórios: {exc}. Reabrindo navegador...")
            page_ref[0] = recover()
            continue

        for tentativa in range(1, POLL_MAX_ATTEMPTS + 1):
            try:
                page_ref[0].locator('//*[@id="btnPesquisar"]').click()
                page_ref[0].wait_for_selector("#tableProcessoReportTmp_data", timeout=10000)
                break
            except Exception as exc:
                if tentativa == POLL_MAX_ATTEMPTS:
                    logger.warning(
                        f"Erro persistente ao atualizar a lista de relatórios: {exc}. "
                        "Reabrindo navegador, refazendo login e voltando à página de relatórios..."
                    )
                    page_ref[0] = recover()
                else:
                    logger.warning(f"Erro temporário ao atualizar lista ({tentativa}/{POLL_MAX_ATTEMPTS}): {exc}")
                    time.sleep(POLL_RETRY_DELAY)

        clip = _clip_selector(relatorio_id)
        if page_ref[0].locator(clip).count() > 0:
            logger.info(f"Relatório '{nome_relatorio}' (ID {relatorio_id}) pronto para download (retry).")
            emit_report(on_report, nome_relatorio, "ready")
            try:
                chave_s3 = _baixar_relatorio(page_ref[0], nome_relatorio, clip, pasta)
                emit_report(on_report, nome_relatorio, "downloaded", chave_s3)
                logger.info(f"Retry bem-sucedido para '{nome_relatorio}'")
                return chave_s3
            except Exception as exc:
                logger.error(f"Erro ao baixar '{nome_relatorio}' durante retry: {exc}")
                emit_report(on_report, nome_relatorio, "error")
                return None
        else:
            logger.info(f"Relatório '{nome_relatorio}' (ID {relatorio_id}) ainda em processamento (retry).")

        page_ref[0].wait_for_timeout(POLL_INTERVAL_MS)

    logger.error(f"Timeout ao fazer retry de '{nome_relatorio}' após {POLL_TIMEOUT}s")
    emit_report(on_report, nome_relatorio, "error")
    return None
