import contextvars
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as DeadlineExceeded

import requests

from connectors.rest_connector import RestConnector
from constants import RECEITA_API_INTERNAL_TOKEN, RECEITA_API_TIMEOUT, RECEITA_API_URL

# O vocabulário da Receita para o nosso. Traduzir aqui é o "desembrulho
# da resposta" que cabe ao connector: o resto do projeto só conhece os
# valores da coluna document_situation.situation.
SITUATIONS = {
    "REGULAR": "regular",
    "SUSPENSA": "suspended",
    "PENDENTE DE REGULARIZACAO": "pending_regularization",
    "CANCELADA": "canceled",
    "TITULAR FALECIDO": "deceased",
    "NULA": "nullified",
}
NOT_FOUND = "not_found"

# As threads que falam com a Receita. UM pool para o processo inteiro, e
# não um por requisição: criar threads a cada Pix custaria mais do que a
# própria consulta. Quatro bastam — a resposta tem prazo curto, e o que
# passar do prazo é abandonado.
#
# A thread só faz a chamada HTTP. Ela nunca toca no banco: a sessão de
# banco desta API vive no contexto da requisição e não é thread-safe.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="receita")


class ReceitaUnavailable(Exception):
    """A Receita não deu uma resposta em que dê para confiar.

    Timeout, conexão recusada, erro 5xx, corpo que não é o esperado: para
    quem chama, é tudo a mesma coisa — não há situação para usar. Quem
    decide o que fazer com isso é o controller (e o que ele faz é não
    reprovar ninguém por falta de dado externo).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReceitaConnector(RestConnector):
    """A consulta da situação cadastral de um CPF/CNPJ na Receita Federal.

    Na sua máquina, quem responde é o mock em mocks/receita/; em produção,
    seria o serviço de verdade. O código não sabe a diferença: muda só a
    RECEITA_API_URL.
    """

    def __init__(self) -> None:
        super().__init__(__name__, RECEITA_API_URL, RECEITA_API_TIMEOUT, RECEITA_API_INTERNAL_TOKEN)

    def get_document_situation(self, document: str) -> str:
        """A situação do documento, com um PRAZO TOTAL de RECEITA_API_TIMEOUT.

        O timeout do `requests` vale para conectar e para ler, mas não para
        descobrir o endereço (DNS). Com o serviço fora do ar, só essa
        descoberta levou 8 segundos num teste — um Pix esperando 8 segundos
        por um dado que nem decide sozinho. Rodando a chamada numa thread
        e esperando no máximo o prazo, o Pix nunca espera mais que isso,
        aconteça o que acontecer na rede.

        O `copy_context` leva o identificador da requisição para a thread:
        sem ele, a linha de log da chamada sairia sem o request_id.
        """
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

        # 404 não é falha: é a Receita dizendo que o documento não existe.
        if response.status == 404:
            return NOT_FOUND

        if response.status != 200:
            raise ReceitaUnavailable(f"http_{response.status}")

        if not isinstance(response.json, dict) or response.json.get("situation") not in SITUATIONS:
            raise ReceitaUnavailable("malformed_response")

        return SITUATIONS[response.json["situation"]]
