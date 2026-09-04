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
