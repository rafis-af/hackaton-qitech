"""Mock da Receita Federal: a situação cadastral de um CPF/CNPJ.

Não existe Receita na sua máquina, e tudo bem: este serviço faz o papel
dela. A resposta sai do PREFIXO do documento (os três primeiros
dígitos), e por isso é previsível — o mesmo documento dá sempre a mesma
resposta, e os testes sabem qual é.

    000  não existe (404)
    001  TITULAR FALECIDO
    002  CANCELADA
    003  NULA
    004  SUSPENSA
    005  PENDENTE DE REGULARIZACAO
    006  lento: demora 3 s para responder (simula a Receita engasgada)
    007  erro 500 (simula a Receita fora do ar)
    008  resposta sem o campo situation (simula uma resposta quebrada)
    qualquer outro  REGULAR

Todos os casos especiais começam com 00. Um CPF de verdade raramente
começa assim, e os geradores de CPF dos testes nunca começam com 0 — a
menos que o teste peça.
"""

import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

SITUATION_BY_PREFIX = {
    "001": "TITULAR FALECIDO",
    "002": "CANCELADA",
    "003": "NULA",
    "004": "SUSPENSA",
    "005": "PENDENTE DE REGULARIZACAO",
}
NOT_FOUND_PREFIX = "000"
SLOW_PREFIX = "006"
SLOW_SECONDS = 3
ERROR_PREFIX = "007"
MALFORMED_PREFIX = "008"


@app.get("/health_check")
def health_check() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


@app.get("/receita/documents/{document}")
def get_document_situation(document: str) -> JSONResponse:
    digits = "".join(character for character in document if character.isdigit())
    prefix = digits[:3]

    if prefix == NOT_FOUND_PREFIX:
        return JSONResponse(status_code=404, content={"message": "Documento não encontrado"})

    if prefix == SLOW_PREFIX:
        time.sleep(SLOW_SECONDS)

    if prefix == ERROR_PREFIX:
        return JSONResponse(status_code=500, content={"message": "Erro interno"})

    if prefix == MALFORMED_PREFIX:
        return JSONResponse(content={"document_number": digits})

    situation = SITUATION_BY_PREFIX.get(prefix, "REGULAR")

    return JSONResponse(content={"document_number": digits, "situation": situation})
