from datetime import timedelta

from tests.utils.payload_generator import NIGHTTIME, PayloadGenerator
from tests.utils.request_generator import RequestGenerator


class ObjectGenerator:
    @staticmethod
    def send_pix(**payload_fields) -> dict:
        payload = PayloadGenerator.create_pix_payload(**payload_fields)

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201, response

        response["pix_id"] = payload["id"]
        return response

    @staticmethod
    def send_two_factor_pix(**payload_fields) -> dict:
        fields = {
            "amount": 150000,
            "transaction_date": NIGHTTIME,
            "destination": PayloadGenerator.create_destination(opening_date=NIGHTTIME - timedelta(days=7)),
        }
        fields.update(payload_fields)

        analysis = ObjectGenerator.send_pix(**fields)
        assert analysis["status"] == "two_factor_required", analysis

        return analysis

    @staticmethod
    def fired_rules(analysis: dict) -> list:
        return [signal["rule"] for signal in analysis["metadata"]["signals"]]

    @staticmethod
    def decision(analysis: dict) -> dict:
        return analysis["metadata"]["decision"]

    @staticmethod
    def risk(analysis: dict) -> dict:
        return analysis["metadata"]["risk"]

    @staticmethod
    def event_types(analysis: dict) -> list:
        return [event["type"] for event in analysis["metadata"]["events"]]

    @staticmethod
    def metric_points(analysis: dict) -> dict:
        return {metric["metric"]: metric["points"] for metric in analysis["metadata"]["risk"]["metrics"]}
