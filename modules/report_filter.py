"""Saneamento incremental dos relatórios do eLaw antes da distribuição."""

import html
import logging
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

_ESCRITORIOS_PERMITIDOS = {
    "mascarenhas barbosa advogados associados",
    "mascarenhas barbosa advogados associados - enter",
}

_COLUNA_ESCRITORIO_POR_RELATORIO = {
    "ESCRITÓRIO - TAREFAS": "F",
    "PAUTA GERAL": "D",
}

_FIM_LINHA = b"</row>"
_INTERVALO_LOG_LINHAS = 10_000
_TEXTO_XML = re.compile(rb"<t(?:\s[^>]*)?>(.*?)</t>", re.DOTALL)
_VALOR_XML = re.compile(rb"<v>(.*?)</v>", re.DOTALL)
_NUMERO_LINHA = re.compile(rb"<row\b[^>]*\br=\"(\d+)\"")
_REFERENCIA_LINHA = re.compile(rb"(<row\b[^>]*\br=\")\d+(\")")
_REFERENCIA_CELULA = re.compile(rb"(<c\b[^>]*\br=\"[A-Z]+)\d+(\")")


def _normalizar(valor: object) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip().casefold()


def _iterar_linhas(arquivo):
    """Produz cada elemento ``<row>`` sem carregar a planilha inteira."""
    buffer = b""
    while bloco := arquivo.read(1024 * 1024):
        buffer += bloco
        while True:
            inicio = buffer.find(b"<row")
            if inicio < 0:
                buffer = buffer[-4:]
                break
            fim = buffer.find(_FIM_LINHA, inicio)
            if fim < 0:
                buffer = buffer[inicio:]
                break
            yield buffer[inicio:fim + len(_FIM_LINHA)]
            buffer = buffer[fim + len(_FIM_LINHA):]


def _carregar_shared_strings(arquivo_zip: zipfile.ZipFile) -> list[str]:
    try:
        with arquivo_zip.open("xl/sharedStrings.xml") as arquivo:
            raiz = ElementTree.parse(arquivo).getroot()
    except KeyError:
        return []
    return ["".join(item.itertext()) for item in raiz]


def _valor_do_escritorio(linha: bytes, coluna: str, shared_strings: list[str]) -> str:
    """Extrai uma célula da linha XML sem instanciar células do Excel."""
    celula = re.search(
        rb"<c\b(?=[^>]*\br=\"" + coluna.encode() + rb"[0-9]+\")[^>]*(?:>(.*?)</c>|/>)",
        linha,
        re.DOTALL,
    )
    if not celula:
        return ""

    corpo = celula.group(1) or b""
    abertura = celula.group(0).split(b">", 1)[0]
    valor = _VALOR_XML.search(corpo)
    if b't="s"' in abertura and valor:
        try:
            return shared_strings[int(valor.group(1))]
        except (IndexError, ValueError):
            return ""

    textos = _TEXTO_XML.findall(corpo)
    if textos:
        return html.unescape(b"".join(textos).decode("utf-8"))
    return html.unescape(valor.group(1).decode("utf-8")) if valor else ""


def _primeira_linha_permitida(
    arquivo_zip: zipfile.ZipFile,
    nome_planilha: str,
    coluna: str,
    shared_strings: list[str],
) -> bytes | None:
    with arquivo_zip.open(nome_planilha) as arquivo:
        for linha in _iterar_linhas(arquivo):
            if _normalizar(_valor_do_escritorio(linha, coluna, shared_strings)) in _ESCRITORIOS_PERMITIDOS:
                return linha
    return None


def _renumerar_linha(linha: bytes, numero: int) -> bytes:
    """Mantém as linhas de dados contíguas depois de remover outras linhas."""
    numero_bytes = str(numero).encode()
    linha = _REFERENCIA_LINHA.sub(lambda match: match.group(1) + numero_bytes + match.group(2), linha, count=1)
    return _REFERENCIA_CELULA.sub(lambda match: match.group(1) + numero_bytes + match.group(2), linha)


def _filtrar_planilha(
    origem: zipfile.ZipFile,
    destino,
    nome_planilha: str,
    coluna: str,
    shared_strings: list[str],
) -> int:
    primeira_permitida = _primeira_linha_permitida(origem, nome_planilha, coluna, shared_strings)
    if primeira_permitida is None:
        raise ValueError(f"Não foi encontrado nenhum processo do Mascarenhas em '{nome_planilha}'.")

    removidas = 0
    buffer = b""
    encontrou_primeira = False
    numero_destino = int(_NUMERO_LINHA.search(primeira_permitida).group(1))
    linhas_analisadas = 0
    linhas_mantidas = 0
    logger.info("Filtrando planilha '%s' (coluna %s)...", nome_planilha, coluna)
    with origem.open(nome_planilha) as arquivo:
        while bloco := arquivo.read(1024 * 1024):
            buffer += bloco
            while True:
                inicio = buffer.find(b"<row")
                if inicio < 0:
                    if len(buffer) > 4:
                        destino.write(buffer[:-4])
                        buffer = buffer[-4:]
                    break
                if inicio:
                    destino.write(buffer[:inicio])
                    buffer = buffer[inicio:]

                fim = buffer.find(_FIM_LINHA)
                if fim < 0:
                    break
                linha = buffer[:fim + len(_FIM_LINHA)]
                buffer = buffer[fim + len(_FIM_LINHA):]
                linhas_analisadas += 1

                permitida = _normalizar(_valor_do_escritorio(linha, coluna, shared_strings)) in _ESCRITORIOS_PERMITIDOS
                if not encontrou_primeira:
                    encontrou_primeira = linha == primeira_permitida
                    destino.write(_renumerar_linha(linha, numero_destino) if encontrou_primeira else linha)
                    if encontrou_primeira:
                        numero_destino += 1
                    linhas_mantidas += 1
                elif permitida:
                    destino.write(_renumerar_linha(linha, numero_destino))
                    numero_destino += 1
                    linhas_mantidas += 1
                else:
                    removidas += 1

                if linhas_analisadas % _INTERVALO_LOG_LINHAS == 0:
                    logger.info(
                        "Planilha '%s': %d linhas analisadas (%d mantidas, %d removidas).",
                        nome_planilha,
                        linhas_analisadas,
                        linhas_mantidas,
                        removidas,
                    )
        destino.write(buffer)
    logger.info(
        "Planilha '%s' concluída: %d linhas analisadas (%d mantidas, %d removidas).",
        nome_planilha,
        linhas_analisadas,
        linhas_mantidas,
        removidas,
    )
    return removidas


def filtrar_processos_mascarenhas(caminho: Path, nome_relatorio: str) -> int:
    """Remove linhas de outros escritórios diretamente do XML interno do XLSX.

    O processamento é incremental e evita ``openpyxl.delete_rows()``, que
    reindexa a planilha inteira para cada linha removida.
    """
    coluna = _COLUNA_ESCRITORIO_POR_RELATORIO.get(nome_relatorio)
    if coluna is None:
        return 0
    if caminho.suffix.lower() != ".xlsx":
        raise ValueError(f"Não é possível filtrar '{nome_relatorio}': formato esperado .xlsx.")

    temporario = caminho.with_name(f"{caminho.stem}.filtrando{caminho.suffix}")
    removidas = 0
    try:
        # Nível 1 equilibra processamento rápido e um XLSX ainda comprimido
        # para não transferir o XML interno (centenas de MB) sem compressão.
        with zipfile.ZipFile(caminho, "r") as origem, zipfile.ZipFile(
            temporario, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1
        ) as destino:
            shared_strings = _carregar_shared_strings(origem)
            planilhas = {
                item.filename
                for item in origem.infolist()
                if item.filename.startswith("xl/worksheets/") and item.filename.endswith(".xml")
            }
            for item in origem.infolist():
                if item.filename not in planilhas:
                    with origem.open(item) as arquivo_origem, destino.open(item.filename, "w") as arquivo_destino:
                        shutil.copyfileobj(arquivo_origem, arquivo_destino, 1024 * 1024)
                    continue
                with destino.open(item.filename, "w") as arquivo_destino:
                    removidas += _filtrar_planilha(
                        origem, arquivo_destino, item.filename, coluna, shared_strings
                    )
        temporario.replace(caminho)
    except Exception:
        temporario.unlink(missing_ok=True)
        raise

    logger.info(
        "Relatório '%s' filtrado por streaming: %d linha(s) de outros escritórios removida(s).",
        nome_relatorio,
        removidas,
    )
    return removidas
