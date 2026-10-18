from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from errors import InvalidDateTime

# Todo "dia" e toda "noite" do motor são os de São Paulo. Um Pix às
# 22:15 de Brasília é noturno mesmo que tenha chegado escrito em UTC
# (01:15 do dia seguinte): o limite noturno do Banco Central é na hora
# local de quem paga.
SAO_PAULO = ZoneInfo("America/Sao_Paulo")

# O Pix começou a funcionar para todo mundo em 16/11/2020. Um Pix com
# data anterior não existe.
PIX_LAUNCH = datetime(2020, 11, 16, tzinfo=SAO_PAULO)
# Uma conta bancária aberta antes de 1900 também não existe.
EARLIEST_ACCOUNT = datetime(1900, 1, 1, tzinfo=SAO_PAULO)
# O limite de cima é largo de propósito: só barra datas absurdas, que
# estourariam as contas de janela.
LATEST_DATE = datetime(2100, 12, 31, 23, 59, 59, tzinfo=SAO_PAULO)


def now() -> datetime:
    return datetime.now(SAO_PAULO)


def to_sao_paulo(moment: datetime) -> datetime:
    return moment.astimezone(SAO_PAULO)


def parse_datetime(field: str, raw_datetime: str, earliest: datetime, latest: datetime) -> datetime:
    """Converte a data com fuso, ou recusa com 422 em vez de 500.

    O schema já garantiu o FORMATO. O que ele não sabe:
      • que fevereiro não tem dia 30 — "2026-02-30T10:00:00-03:00" passa
        pelo regex e para aqui;
      • que uma data válida pode virar inválida ao trocar de fuso.
        "0001-01-01T00:00:00+14:00" em UTC é o ano zero, que não existe, e
        "9999-12-31T23:59:59-14:00" é o ano 10000, que o Python não
        representa. Sem esta conversão aqui, na porta, as duas estourariam
        lá dentro, na conta das janelas, como 500;
      • que a data faz sentido para o campo — `earliest` e `latest`.
    """
    try:
        moment = datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
        moment.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise InvalidDateTime(field, raw_datetime)

    if not earliest <= moment <= latest:
        raise InvalidDateTime(field, raw_datetime)

    return moment


def to_local_isoformat(moment: datetime) -> str:
    return to_sao_paulo(moment).isoformat()
