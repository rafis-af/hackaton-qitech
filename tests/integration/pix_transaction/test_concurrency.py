from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from tests.utils import NIGHTTIME, ObjectGenerator, PayloadGenerator, RandomGenerator, RequestGenerator


def run_at_the_same_time(payloads: list) -> list:
    with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
        futures = [executor.submit(RequestGenerator.POST_pix_transaction, payload) for payload in payloads]
        return [future.result() for future in futures]


class TestConcurrency:
    def test_same_pix_at_the_same_time_is_analyzed_once(self):
        payload = PayloadGenerator.create_pix_payload()

        results = run_at_the_same_time([payload for _copy in range(5)])

        statuses = sorted(status for status, _response in results)
        assert statuses == [200, 200, 200, 200, 201]
        assert len({response["key"] for _status, response in results}) == 1

    def test_two_answers_to_the_same_two_factor_at_the_same_time(self):
        analysis = ObjectGenerator.send_two_factor_pix()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(RequestGenerator.POST_two_factor, analysis["pix_id"], {"result": result})
                for result in ["confirmed", "failed"]
            ]
            results = [future.result() for future in futures]

        statuses = sorted(status for status, _response in results)
        assert statuses == [201, 409]

    def test_same_id_with_different_data_at_the_same_time(self):
        pix_id = f"pix-{RandomGenerator.generate_cpf()}"
        payloads = [PayloadGenerator.create_pix_payload(pix_id=pix_id) for _payer in range(2)]

        results = run_at_the_same_time(payloads)

        statuses = sorted(status for status, _response in results)
        assert statuses == [201, 409]

    def test_simultaneous_pix_of_one_payer_see_each_other(self):
        payer = RandomGenerator.generate_cpf()
        payloads = [
            PayloadGenerator.create_pix_payload(
                payer_document=payer, amount=90000, transaction_date=NIGHTTIME + timedelta(seconds=1)
            )
            for _pix in range(6)
        ]

        results = run_at_the_same_time(payloads)

        statuses = sorted(response["status"] for _status, response in results)
        assert statuses == ["approved"] + ["rejected"] * 4 + ["two_factor_required"]
