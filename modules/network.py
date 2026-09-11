"""Estado mínimo de conectividade exigido para o painel operar."""

from modules.updater import UpdateError, check


def check_connectivity() -> tuple[bool, str]:
    try:
        check()
    except UpdateError as exc:
        return False, str(exc)
    return True, ""
