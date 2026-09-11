"""Cancela no MongoDB todas as execuções persistidas como em andamento.

Uso:
    python cancelar_execucoes_em_andamento.py

O script usa a variável MONGO_URI do arquivo .env do projeto. Ele altera
somente documentos da coleção ``runs`` cujo status seja ``running``.
"""

from modules.history import cancelar_execucoes_em_andamento


def main() -> None:
    quantidade = cancelar_execucoes_em_andamento()
    print(f"{quantidade} execução(ões) marcada(s) como cancelada(s).")


if __name__ == "__main__":
    main()
