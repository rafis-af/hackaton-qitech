from datetime import datetime, time
from zoneinfo import ZoneInfo

from errors import InvalidDateTime

# Todo "dia" e toda "noite" do motor são os de São Paulo. Um Pix às
# 22:15 de Brasília é noturno mesmo que tenha chegado escrito em UTC
# (01:15 do dia seguinte): o limite noturno do Banco Central é na hora
# local de quem paga.
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def to_sao_paulo(moment: datetime) -> datetime:
    return moment.astimezone(SAO_PAULO)


def day_start(moment: datetime) -> datetime:
    """A meia-noite, em São Paulo, do dia em que `moment` aconteceu."""
    local_moment = to_sao_paulo(moment)
    return datetime.combine(local_moment.date(), time.min, tzinfo=SAO_PAULO)


def parse_datetime(field: str, raw_datetime: str) -> datetime:
    """Converte a data com fuso, ou recusa com 422 em vez de 500.

    O schema já garantiu o FORMATO. O que ele não sabe é que fevereiro
    não tem dia 30 — "2026-02-30T10:00:00-03:00" passa pelo regex e
    para aqui.
    """
    try:
        return datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidDateTime(field, raw_datetime)


def to_local_isoformat(moment: datetime) -> str:
    return to_sao_paulo(moment).isoformat()
