from os import environ

from tests.utils.requisition import ClientRequisition


INTERNAL_TOKEN = environ.get("INTERNAL_TOKEN", "default_token")


class RequestGenerator:
    @staticmethod
    def POST_pix_transaction(pix_payload: dict, headers: dict = None) -> tuple:
        if headers is None:
            headers = {"INTERNAL-TOKEN": INTERNAL_TOKEN}

        response = ClientRequisition.send("POST", "/pix/transaction", payload=pix_payload, headers=headers)
        return response.response_status, response.response_json

    @staticmethod
    def GET_pix_transaction(pix_id: str) -> tuple:
        response = ClientRequisition.send(
            "GET",
            f"/pix/transaction/{pix_id}",
            headers={"INTERNAL-TOKEN": INTERNAL_TOKEN},
        )
        return response.response_status, response.response_json

    @staticmethod
    def POST_two_factor(pix_id: str, two_factor_payload: dict) -> tuple:
        response = ClientRequisition.send(
            "POST",
            f"/pix/transaction/{pix_id}/two-factor",
            payload=two_factor_payload,
            headers={"INTERNAL-TOKEN": INTERNAL_TOKEN},
        )
        return response.response_status, response.response_json
