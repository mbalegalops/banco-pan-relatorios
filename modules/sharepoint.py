"""Salva relatórios em caminho local sincronizado com SharePoint via OneDrive."""

import logging
import os
import shutil
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SHAREPOINT_MAX_RETRIES = 3
SHAREPOINT_RETRY_DELAY = 2.0


def obter_pasta_sharepoint() -> Path:
    """Retorna caminho da pasta sincronizada com SharePoint.

    Usa o nome do usuário Windows automaticamente para funcionar em qualquer computador."""
    usuario = os.getenv('USERNAME', 'usuario')
    caminho_base = Path(
        f"C:\\Users\\{usuario}\\Mascarenhas Barbosa Advogados\\MBA - Robô de Checagem"
    )
    return caminho_base


def enviar_relatorio_sharepoint(
    caminho_arquivo: str | Path,
    pasta_data: str,
    on_complete: Optional[Callable[[bool, str], None]] = None,
) -> None:
    """Copia arquivo para pasta local sincronizada com SharePoint com retry automático.

    Args:
        caminho_arquivo: Caminho do arquivo de origem (temp)
        pasta_data: Pasta de destino no formato AAAA-MM-DD
        on_complete: Callback chamado ao finalizar: (sucesso: bool, mensagem: str)

    Roda em thread separada para não bloquear pipeline."""
    def _copiar_com_retry():
        try:
            caminho_arquivo_path = Path(caminho_arquivo)
            if not caminho_arquivo_path.exists():
                raise FileNotFoundError(f"Arquivo não encontrado: {caminho_arquivo}")

            pasta_destino = obter_pasta_sharepoint() / pasta_data
            pasta_destino.mkdir(parents=True, exist_ok=True)

            nome_arquivo = caminho_arquivo_path.name
            caminho_destino = pasta_destino / nome_arquivo

            for tentativa in range(1, SHAREPOINT_MAX_RETRIES + 1):
                try:
                    shutil.copy2(str(caminho_arquivo_path), str(caminho_destino))
                    logger.info(f"Arquivo '{nome_arquivo}' copiado para SharePoint: {caminho_destino}")
                    if on_complete:
                        on_complete(True, f"Salvo em SharePoint: {caminho_destino}")
                    return
                except Exception as exc:
                    if tentativa == SHAREPOINT_MAX_RETRIES:
                        logger.error(
                            f"Falha ao copiar '{nome_arquivo}' para SharePoint após "
                            f"{SHAREPOINT_MAX_RETRIES} tentativas: {exc}"
                        )
                        if on_complete:
                            on_complete(False, f"Erro ao salvar em SharePoint: {exc}")
                        return
                    else:
                        logger.warning(
                            f"Erro ao copiar '{nome_arquivo}' para SharePoint "
                            f"({tentativa}/{SHAREPOINT_MAX_RETRIES}): {exc}. "
                            f"Retentando em {SHAREPOINT_RETRY_DELAY}s..."
                        )
                        time.sleep(SHAREPOINT_RETRY_DELAY)
        except Exception as exc:
            logger.error(f"Erro ao copiar para SharePoint: {exc}")
            if on_complete:
                on_complete(False, f"Erro ao copiar: {exc}")

    thread = threading.Thread(target=_copiar_com_retry, daemon=True)
    thread.start()
