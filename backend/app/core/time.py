from datetime import datetime
from zoneinfo import ZoneInfo

# Fuso horário oficial de Brasília (BRT, UTC-3)
BRT = ZoneInfo("America/Sao_Paulo")

def get_now() -> datetime:
    """Retorna o datetime atual no fuso horário de Brasília."""
    return datetime.now(BRT)

def get_now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Retorna a string da data/hora atual no fuso de Brasília."""
    return get_now().strftime(fmt)
