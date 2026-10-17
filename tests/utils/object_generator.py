from tests.utils.payload_generator import PayloadGenerator
from tests.utils.request_generator import RequestGenerator


class ObjectGenerator:
    @staticmethod
    def send_pix(**payload_fields) -> dict:
        """Manda um Pix e devolve a análise. Falha alto se o POST não for 201."""
        payload = PayloadGenerator.create_pix_payload(**payload_fields)

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201, response

        response["pix_id"] = payload["id"]
        return response

    @staticmethod
    def fired_rules(analysis: dict) -> list:
        return [signal["rule"] for signal in analysis["metadata"]["signals"]]
