"""Publica o instalador e o manifesto de atualização diretamente no S3.

Uso: ``python publish_release.py <versão> <caminho-do-instalador>``.
As credenciais AWS são lidas do .env local; o bucket precisa permitir leitura
anônima (ou por uma política corporativa) apenas do prefixo releases/.
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
from dotenv import load_dotenv

from modules.paths import APP_DIR

BUCKET = "mascarenhas-lake"
PREFIX = "operacional/orquestra/banco-pan/relatorios/releases"
DOWNLOAD_BASE_URL = f"https://{BUCKET}.s3.amazonaws.com/{PREFIX}"


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Uso: publish_release.py <versão> <instalador>")
    version, installer = sys.argv[1], Path(sys.argv[2])
    if not installer.is_file():
        raise SystemExit(f"Instalador não encontrado: {installer}")

    load_dotenv(APP_DIR / ".env")
    key = f"{PREFIX}/RelatoriosPan-{version}-setup.exe"
    sha256 = hashlib.sha256(installer.read_bytes()).hexdigest()
    manifest = {
        "version": version,
        "installerUrl": f"{DOWNLOAD_BASE_URL}/RelatoriosPan-{version}-setup.exe",
        "sha256": sha256,
        "releaseNotes": "",
        "mandatory": False,
        "publishedAt": datetime.now(timezone.utc).isoformat(),
    }
    client = boto3.client("s3")
    # O manifesto é enviado por último: assim ele nunca aponta para um
    # instalador que ainda não foi publicado.
    client.upload_file(str(installer), BUCKET, key, ExtraArgs={"ContentType": "application/vnd.microsoft.portable-executable"})
    client.put_object(Bucket=BUCKET, Key=f"{PREFIX}/stable.json",
                      Body=json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
                      ContentType="application/json", CacheControl="no-cache")
    print(f"Release {version} publicada em s3://{BUCKET}/{key}")


if __name__ == "__main__":
    main()
