import contextvars
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as DeadlineExceeded

import requests

from connectors.rest_connector import RestConnector
from constants import RECEITA_API_INTERNAL_TOKEN, RECEITA_API_TIMEOUT, RECEITA_API_URL

SITUATIONS = {
    "REGULAR": "regular",
    "SUSPENSA": "suspended",
    "PENDENTE DE REGULARIZACAO": "pending_regularization",
    "CANCELADA": "canceled",
    "TITULAR FALECIDO": "deceased",
    "NULA": "nullified",
}
NOT_FOUND = "not_found"

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="receita")


class ReceitaUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReceitaConnector(RestConnector):
    def __init__(self) -> None:
        super().__init__(__name__, RECEITA_API_URL, RECEITA_API_TIMEOUT, RECEITA_API_INTERNAL_TOKEN)

    def get_document_situation(self, document: str) -> str:
        context = contextvars.copy_context()
        future = _executor.submit(context.run, self._request_situation, document)

        try:
            return future.result(timeout=RECEITA_API_TIMEOUT)
        except DeadlineExceeded:
            raise ReceitaUnavailable("timeout")

    def _request_situation(self, document: str) -> str:
        try:
            response = self.send(f"/receita/documents/{document}", "GET")
        except requests.Timeout:
            raise ReceitaUnavailable("timeout")
        except requests.RequestException:
            raise ReceitaUnavailable("connection_error")

        if response.status == 404:
            return NOT_FOUND

        if response.status != 200:
            raise ReceitaUnavailable(f"http_{response.status}")

        if not isinstance(response.json, dict) or response.json.get("situation") not in SITUATIONS:
            raise ReceitaUnavailable("malformed_response")

        return SITUATIONS[response.json["situation"]]
