from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from errors import InvalidDateTime

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

PIX_LAUNCH = datetime(2020, 11, 16, tzinfo=SAO_PAULO)
EARLIEST_ACCOUNT = datetime(1900, 1, 1, tzinfo=SAO_PAULO)
LATEST_DATE = datetime(2100, 12, 31, 23, 59, 59, tzinfo=SAO_PAULO)


def now() -> datetime:
    return datetime.now(SAO_PAULO)


def to_sao_paulo(moment: datetime) -> datetime:
    return moment.astimezone(SAO_PAULO)


def parse_datetime(field: str, raw_datetime: str, earliest: datetime, latest: datetime) -> datetime:
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
