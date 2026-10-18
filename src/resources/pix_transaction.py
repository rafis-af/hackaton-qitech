from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from controllers import PixTransactionController
from utils.schema_handler import SchemaHandler


class PixTransactionResource:
    """A porta de entrada HTTP do antifraude.

    Como no Base Service: confere o corpo, chama UM controller, devolve
    a resposta com o status certo. Nenhuma regra mora aqui.
    """

    @SchemaHandler.validate("post_pix_transaction.json")
    def on_post(self, payload: dict) -> JSONResponse:
        controller = PixTransactionController()
        result = controller.analyze(payload)

        # 201 para a análise nova; 200 para o reenvio, que devolve a
        # análise que já existia sem criar nada.
        status_code = http_status.HTTP_201_CREATED
        if result["is_replay"]:
            status_code = http_status.HTTP_200_OK

        return JSONResponse(
            content=jsonable_encoder(result["analysis"]),
            status_code=status_code,
        )

    def on_get_by_id(self, pix_id: str) -> JSONResponse:
        controller = PixTransactionController()
        analysis = controller.get(pix_id)

        return JSONResponse(
            content=jsonable_encoder(analysis),
            status_code=http_status.HTTP_200_OK,
        )

    @SchemaHandler.validate("post_two_factor.json")
    def on_post_two_factor(self, pix_id: str, payload: dict) -> JSONResponse:
        controller = PixTransactionController()
        analysis = controller.answer_two_factor(pix_id, payload["result"])

        return JSONResponse(
            content=jsonable_encoder(analysis),
            status_code=http_status.HTTP_201_CREATED,
        )
