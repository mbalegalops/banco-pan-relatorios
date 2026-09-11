"""Consulta e instala atualizações publicadas diretamente no S3."""

import hashlib
import json
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

from modules.paths import APP_DIR

DEFAULT_MANIFEST_URL = "https://mascarenhas-lake.s3.amazonaws.com/operacional/orquestra/banco-pan/relatorios/releases/stable.json"
TIMEOUT_S = 8


class UpdateError(RuntimeError):
    """O manifesto ou o instalador não pôde ser obtido ou validado."""


@dataclass(frozen=True)
class Release:
    version: str
    installer_url: str
    sha256: str
    release_notes: str = ""
    mandatory: bool = False


def _manifest_url() -> str:
    return dotenv_values(APP_DIR / ".env").get("UPDATE_MANIFEST_URL") or DEFAULT_MANIFEST_URL


def local_version() -> str:
    try:
        return (APP_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def _version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError as exc:
        raise UpdateError(f"Versão inválida no manifesto: {value!r}") from exc


def is_newer(remote: str, local: str) -> bool:
    remote_parts, local_parts = _version_key(remote), _version_key(local)
    length = max(len(remote_parts), len(local_parts))
    return remote_parts + (0,) * (length - len(remote_parts)) > local_parts + (0,) * (length - len(local_parts))


def check() -> Optional[Release]:
    """Retorna a release; uma falha também indica falta de conectividade."""
    try:
        with urllib.request.urlopen(_manifest_url(), timeout=TIMEOUT_S) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise UpdateError("Sem conexão com a internet ou com o servidor de atualizações.") from exc
    required = ("version", "installerUrl", "sha256")
    if not all(isinstance(payload.get(key), str) and payload[key].strip() for key in required):
        raise UpdateError("Manifesto de atualização inválido.")
    release = Release(payload["version"].strip(), payload["installerUrl"].strip(), payload["sha256"].strip().lower(), str(payload.get("releaseNotes", "")), bool(payload.get("mandatory", False)))
    _version_key(release.version)
    if len(release.sha256) != 64 or any(char not in "0123456789abcdef" for char in release.sha256):
        raise UpdateError("Hash SHA-256 inválido no manifesto.")
    return release


def download(release: Release) -> Path:
    """Baixa o instalador temporariamente e valida seu SHA-256."""
    destination = Path(tempfile.gettempdir()) / f"RelatoriosPan-{release.version}-setup.exe"
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(release.installer_url, timeout=TIMEOUT_S) as response, open(destination, "wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise UpdateError("Não foi possível baixar a atualização.") from exc
    if digest.hexdigest().lower() != release.sha256:
        destination.unlink(missing_ok=True)
        raise UpdateError("A validação de segurança da atualização falhou.")
    return destination
