"""Servidor web (FastAPI) do painel de Relatórios.

Substitui a GUI Tkinter (gui.py, removido) — mesmo desenho usado no
Checagem (checagem/web.py): a orquestração (thread de trabalho, log por
execução, cancelamento cooperativo) fica aqui; a lógica de automação
(pipeline.py, pesquisar.py, modules/*) não muda nada, só a camada de
apresentação."""

import logging
import re
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import modules.history as history
import modules.storage as storage
from pipeline import executar
from modules.browser import SessaoElaw
from modules.cancel import ExecucaoCancelada
from modules.login import credenciais_atuais, salvar_credenciais
from modules.paths import APP_DIR

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LOGS_DIR = APP_DIR / "logs" / "runs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

STATIC_DIR = Path(__file__).parent / "static"

WS_TICK_S = 0.35
_LOG_LINE_RE = re.compile(r"^(.*?) \[(\w+)\] (.*)$")


def _parse_log_line(line: str) -> Optional[tuple[str, str, str]]:
    m = _LOG_LINE_RE.match(line)
    if not m:
        return None
    prefix, level, message = m.groups()
    time_part = prefix.split(" ")[-1].split(",")[0]
    return time_part, level, message


def fmt_data(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return d.strftime("%d/%m/%Y %H:%M")


# ---------------------------------------------------------------------------
# Log handler: grava cada execução em logs/runs/{id}.log e acumula cada
# linha (já separada em tempo/nível/mensagem) em state.live_log_lines, lida
# pelo WebSocket. Anexado só durante a execução.
# ---------------------------------------------------------------------------

class _RunLogHandler(logging.Handler):
    def __init__(self, log_path: Path, state: "ServerState") -> None:
        super().__init__()
        # "w": cada run_id é sempre uma execução nova, nunca reaproveita
        # conteúdo de uma execução antiga com o mesmo id.
        self.file = open(log_path, "w", encoding="utf-8")
        self.state = state
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.file.write(self.format(record) + "\n")
            self.file.flush()
        except Exception:
            pass
        linha = {
            "t": datetime.now().strftime("%H:%M:%S"),
            "lvl": record.levelname,
            "msg": record.getMessage(),
        }
        with self.state.lock:
            self.state.live_log_lines.append(linha)

    def close(self) -> None:
        try:
            self.file.close()
        finally:
            super().close()


class ServerState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.worker_thread: Optional[threading.Thread] = None
        self.cancel_event: Optional[threading.Event] = None
        self.sessao: Optional[SessaoElaw] = None
        self.live_run_id: Optional[int] = None
        self.live_log_lines: list[dict] = []
        # Chaves S3 já registradas durante a execução em andamento (antes
        # mesmo dela terminar) — permite o botão de download de cada
        # relatório aparecer assim que ele fica pronto, sem esperar o fim.
        self.live_downloads: dict[str, str] = {}


state = ServerState()

app = FastAPI(title="Relatórios · Pan")


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """Impede o navegador de cachear `index.html`/`static/*` entre
    execuções do app — sem isso, uma atualização do app.js pode não surtir
    efeito até um hard-refresh manual (Ctrl+F5)."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ------------------------------------------------------------------- worker

def _worker(run_id: int, cancel_event: threading.Event) -> None:
    log_path = LOGS_DIR / f"{run_id}.log"
    handler = _RunLogHandler(log_path, state)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    def on_report(name: str, status: str, chave: Optional[str] = None) -> None:
        if status == "downloaded" and chave:
            with state.lock:
                state.live_downloads[name] = chave
            # Chamada de rede — roda numa thread de fundo pra não segurar o
            # worker principal; o estado em memória acima já libera o botão
            # de download imediatamente, sem esperar a escrita terminar.
            threading.Thread(target=history.registrar_download, args=(run_id, name, chave), daemon=True).start()

    def on_sessao(sessao: SessaoElaw) -> None:
        with state.lock:
            state.sessao = sessao

    error: Optional[str] = None
    cancelado = False
    downloads: dict[str, str] = {}
    try:
        downloads = executar(
            on_report=on_report, cancel_event=cancel_event, on_sessao=on_sessao, run_id=run_id,
        )
    except ExecucaoCancelada:
        cancelado = True
        logger.info("Execução #%d cancelada pelo usuário.", run_id)
    except Exception as exc:
        if cancel_event.is_set():
            # Navegador fechado à força pelo cancelamento — a exceção que
            # isso gera no Playwright varia, mas a causa é sempre essa.
            cancelado = True
            logger.info("Execução #%d cancelada pelo usuário.", run_id)
        else:
            error = str(exc)
            logger.exception("Execução #%d falhou", run_id)
    finally:
        root_logger.removeHandler(handler)
        handler.close()

        # O log é enviado pro S3 (e a URI, gravada no Mongo) assim que a
        # execução termina — a GUI sempre busca o log de lá, nunca do disco
        # local, pra ficar visível de qualquer máquina, não só de quem
        # rodou. O arquivo local (escrito por _RunLogHandler durante a
        # execução) só existe de passagem: some depois do upload; se o
        # upload falhar, fica pra trás como último recurso de diagnóstico.
        try:
            pasta = storage.pasta_do_dia()
            log_uri = storage.enviar_log(log_path, pasta, run_id)
            history.registrar_log(run_id, log_uri)
            log_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"Não foi possível enviar o log da execução #{run_id} para o S3: {exc}")

        history.finish_run_record(run_id, error, downloads, cancelado=cancelado)
        with state.lock:
            state.worker_thread = None
            state.cancel_event = None
            state.sessao = None
            state.live_run_id = None
            state.live_downloads = {}


def _start_run() -> dict:
    with state.lock:
        if state.worker_thread is not None:
            raise HTTPException(409, "Já existe uma execução em andamento.")

        email, tem_senha = credenciais_atuais()
        if not email or not tem_senha:
            raise HTTPException(400, 'Credenciais do eLaw não configuradas — clique em "Credenciais" antes de executar.')

        run_id = history.create_run_record()
        cancel_event = threading.Event()
        state.cancel_event = cancel_event
        state.sessao = None
        state.live_run_id = run_id
        state.live_log_lines = []
        state.live_downloads = {}
        thread = threading.Thread(target=_worker, args=(run_id, cancel_event), daemon=True)
        state.worker_thread = thread

    thread.start()
    return {"ok": True, "execucao_id": run_id}


# ------------------------------------------------------------------- rotas

@app.post("/api/executar")
def api_executar() -> dict:
    return _start_run()


@app.post("/api/cancelar")
def api_cancelar() -> dict:
    with state.lock:
        cancel_event = state.cancel_event
        sessao = state.sessao
        run_id = state.live_run_id
        running = state.worker_thread is not None
    if not running or cancel_event is None:
        raise HTTPException(409, "Não há execução em andamento.")
    logger.warning("Cancelamento solicitado para a execução #%s.", run_id)
    cancel_event.set()
    if sessao is not None:
        sessao.cancelar()
    return {"ok": True}


def _run_publico(r: dict, live_run_id: Optional[int], running: bool, live_downloads: dict[str, str]) -> dict:
    status = "running" if (r["id"] == live_run_id and running) else r["status"]
    downloads = dict(r.get("downloads") or {})
    if r["id"] == live_run_id:
        downloads.update(live_downloads)
    return {
        "id": r["id"],
        "inicio": fmt_data(r["started_at"]),
        "fim": fmt_data(r.get("finished_at")),
        "usuario": r.get("usuario") or "-",
        "status": status,
        "relatorios": sorted(downloads.keys()),
        "erro": r.get("error") if status == "error" else None,
    }


@app.get("/api/execucoes")
def api_execucoes() -> dict:
    email, tem_senha = credenciais_atuais()
    config_ok = bool(email and tem_senha)

    with state.lock:
        live_run_id = state.live_run_id
        running = state.worker_thread is not None
        live_downloads = dict(state.live_downloads)

    runs = history.list_runs()
    execucoes = [_run_publico(r, live_run_id, running, live_downloads) for r in runs]
    return {
        "execucoes": execucoes,
        "live_execucao_id": live_run_id,
        "running": running,
        "config_ok": config_ok,
    }


def _texto_log_do_s3(run_id: int) -> Optional[str]:
    run = history.get_run(run_id)
    log_uri = run.get("log_uri") if run else None
    if not log_uri:
        return None
    try:
        return storage.obter_texto(log_uri)
    except Exception as exc:
        logger.warning(f"Falha ao buscar no S3 o log da execução #{run_id}: {exc}")
        return None


@app.get("/api/logs/{run_id}")
def api_logs(run_id: int) -> dict:
    texto = _texto_log_do_s3(run_id)
    if texto is None:
        return {"linhas": [], "encontrado": False}
    linhas = []
    for line in texto.splitlines():
        parsed = _parse_log_line(line)
        if parsed:
            t, lvl, msg = parsed
            linhas.append({"t": t, "lvl": lvl, "msg": msg})
        elif line.strip():
            linhas.append({"t": "", "lvl": "", "msg": line})
    return {"linhas": linhas, "encontrado": True}


@app.get("/api/logs/{run_id}/download")
def api_logs_download(run_id: int):
    texto = _texto_log_do_s3(run_id)
    if texto is None:
        raise HTTPException(404, "Log não encontrado para essa execução.")
    return Response(
        content=texto,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.log"'},
    )


@app.get("/api/relatorios/{run_id}/{nome}")
def api_relatorio_download(run_id: int, nome: str):
    """Baixa do S3 um dos relatórios (DADOS DO PROCESSO / ESCRITÓRIO -
    TAREFAS / PAUTA GERAL) gerados numa execução — os botões de download da
    interface apontam pra cá."""
    run = history.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Execução não encontrada.")
    chave = (run.get("downloads") or {}).get(nome)
    if not chave:
        raise HTTPException(404, "Relatório não encontrado para essa execução.")

    conteudo = storage.obter_bytes(chave)
    filename = chave.rsplit("/", 1)[-1]
    return Response(
        content=conteudo,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/credenciais")
def api_credenciais_get() -> dict:
    email, tem_senha = credenciais_atuais()
    return {"email": email, "tem_senha": tem_senha}


@app.post("/api/credenciais")
def api_credenciais_post(payload: dict) -> dict:
    email = (payload.get("email") or "").strip()
    senha = payload.get("senha") or None
    if not email:
        raise HTTPException(400, "Preencha o e-mail.")
    _, tem_senha = credenciais_atuais()
    if not senha and not tem_senha:
        raise HTTPException(400, "Preencha a senha.")
    salvar_credenciais(email, senha)
    return {"ok": True}


def _retry_worker(run_id: int, nome_relatorio: str, relatorio_id: str) -> None:
    """Worker thread para fazer retry de um relatório que falhou."""
    from modules.login import LoginConfig
    from playwright.sync_api import sync_playwright

    import modules.download as download
    from modules.browser import SessaoElaw

    log_path = LOGS_DIR / f"{run_id}_retry_{nome_relatorio.replace(' ', '_')}.log"
    handler = _RunLogHandler(log_path, state)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    try:
        logger.info(f"Iniciando retry de '{nome_relatorio}' (ID {relatorio_id}) para execução #{run_id}...")
        config = LoginConfig.from_env()
        pasta = storage.pasta_do_dia()

        with sync_playwright() as p:
            sessao = SessaoElaw(p, config)
            try:
                chave_s3 = download.fazer_download_relatorio_individual(
                    sessao.page, nome_relatorio, relatorio_id, pasta, sessao.recover,
                )
                if chave_s3:
                    history.registrar_download(run_id, nome_relatorio, chave_s3)
                    history.remover_relatorio_falhado(run_id, nome_relatorio)
                    logger.info(f"Retry bem-sucedido: '{nome_relatorio}' salvo em S3")
                else:
                    logger.error(f"Retry falhou: '{nome_relatorio}' não pôde ser baixado")
            finally:
                sessao.close()
    except Exception as exc:
        logger.exception(f"Erro durante retry de '{nome_relatorio}': {exc}")
    finally:
        root_logger.removeHandler(handler)
        handler.close()


@app.post("/api/retentar/{run_id}/{nome_relatorio}")
def api_retentar(run_id: int, nome_relatorio: str) -> dict:
    """Tenta fazer download de um relatório que falhou anteriormente."""
    with state.lock:
        running = state.worker_thread is not None

    if running:
        raise HTTPException(409, "Uma execução já está em andamento. Aguarde terminar para retentar um relatório.")

    run = history.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Execução não encontrada.")

    failed_reports = run.get("failed_reports") or {}
    if nome_relatorio not in failed_reports:
        raise HTTPException(404, f"Relatório '{nome_relatorio}' não está marcado como falhado nessa execução.")

    relatorio_id = failed_reports[nome_relatorio]

    logger.info(f"Iniciando retry para '{nome_relatorio}' (ID {relatorio_id}) da execução #{run_id}")

    thread = threading.Thread(target=_retry_worker, args=(run_id, nome_relatorio, relatorio_id), daemon=True)
    thread.start()

    return {"ok": True, "status": "retrying"}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    import asyncio

    await websocket.accept()
    last_sent = 0
    last_run_id: Optional[int] = None
    try:
        while True:
            with state.lock:
                live_run_id = state.live_run_id
                if live_run_id != last_run_id:
                    last_sent = 0
                    last_run_id = live_run_id
                novas_linhas = state.live_log_lines[last_sent:]
                last_sent = len(state.live_log_lines)
                running = state.worker_thread is not None
                live_downloads = dict(state.live_downloads)

            await websocket.send_json({
                "live_execucao_id": live_run_id,
                "running": running,
                "novas_linhas": novas_linhas,
                "live_downloads": live_downloads,
            })
            await asyncio.sleep(WS_TICK_S)
    except WebSocketDisconnect:
        return


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ------------------------------------------------------------------- boot

def _find_free_port() -> int:
    with socket(AF_INET, SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    import uvicorn

    history.init_db()

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}/"

    def open_browser() -> None:
        time.sleep(0.8)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()
    logger.info(f"Servindo Relatórios · Pan em {url}")
    # log_config=None: no build windowed do PyInstaller (console=False)
    # sys.stdout/stderr chegam como None, e o setup de logging padrão do
    # uvicorn quebra ao chamar `.isatty()` neles (AttributeError dentro de
    # dictConfig, virando "ValueError: Unable to configure formatter
    # 'default'"). Sem log_config o uvicorn não mexe no logging - ele
    # continua saindo pelo `logging.basicConfig` já configurado acima.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)


if __name__ == "__main__":
    main()
