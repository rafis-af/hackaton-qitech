import logging
import sys

from constants import APP_ENV, SERVICE_NAME
from utils.request_context import get_request_id


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s [%(request_id)s] - %(message)s"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def setup_logging() -> None:
    level = logging.INFO
    if APP_ENV.upper() in ("LOCAL", "TEST"):
        level = logging.DEBUG

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))

    handler.addFilter(RequestIdFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [handler]

    for biblioteca_falante in ["urllib3"]:
        logging.getLogger(biblioteca_falante).setLevel(logging.CRITICAL)


def get_logger(class_name: str) -> logging.Logger:
    return logging.getLogger(f"{SERVICE_NAME}.{class_name}")
