from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from tests.utils import NIGHTTIME, PayloadGenerator, RandomGenerator, RequestGenerator


def run_at_the_same_time(payloads: list) -> list:
    """Dispara todos os POSTs juntos, cada um na sua thread, e devolve (status, corpo) de cada."""
    with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
        futures = [executor.submit(RequestGenerator.POST_pix_transaction, payload) for payload in payloads]
        return [future.result() for future in futures]


class TestConcurrency:
    def test_same_pix_at_the_same_time_is_analyzed_once(self):
        """Cinco cópias do mesmo Pix juntas: uma analisa (201), as outras devolvem a mesma análise (200)."""
        payload = PayloadGenerator.create_pix_payload()

        results = run_at_the_same_time([payload for _copy in range(5)])

        statuses = sorted(status for status, _response in results)
        assert statuses == [200, 200, 200, 200, 201]
        assert len({response["key"] for _status, response in results}) == 1

    def test_simultaneous_pix_of_one_payer_see_each_others_points(self):
        """Seis Pix de fracionamento ao mesmo tempo dão o mesmo resultado que um atrás do outro.

        Sem a trava por pagador, os seis leriam o score zerado ao mesmo
        tempo, e todos passariam com 2FA. Com a trava, cada um enxerga os
        pontos do anterior — e dois são reprovados, como na sequência do
        test_pix_transaction_rules.
        """
        payer = RandomGenerator.generate_cpf()
        payloads = [
            PayloadGenerator.create_pix_payload(
                payer_document=payer, amount=90000, transaction_date=NIGHTTIME + timedelta(seconds=1)
            )
            for _pix in range(6)
        ]

        results = run_at_the_same_time(payloads)

        statuses = sorted(response["status"] for _status, response in results)
        assert statuses == ["rejected"] * 2 + ["two_factor_required"] * 4
