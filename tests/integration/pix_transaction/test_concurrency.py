from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from tests.utils import NIGHTTIME, ObjectGenerator, PayloadGenerator, RandomGenerator, RequestGenerator


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

    def test_two_answers_to_the_same_two_factor_at_the_same_time(self):
        """Confirmado e falhou chegando juntos: uma resposta fecha a pendência, a outra leva 409."""
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
        """O mesmo id por dois pagadores diferentes, juntos: um analisa, o outro leva 409."""
        pix_id = f"pix-{RandomGenerator.generate_cpf()}"
        payloads = [PayloadGenerator.create_pix_payload(pix_id=pix_id) for _payer in range(2)]

        results = run_at_the_same_time(payloads)

        statuses = sorted(status for status, _response in results)
        assert statuses == [201, 409]

    def test_simultaneous_pix_of_one_payer_see_each_other(self):
        """Seis Pix do mesmo pagador ao mesmo tempo dão o mesmo resultado que um atrás do outro.

        Sem a trava por pagador, os seis leriam o histórico vazio ao mesmo
        tempo: nenhum contaria na velocidade do outro, nenhum herdaria
        risco, e todos sairiam aprovados. Com a trava, cada um enxerga os
        anteriores — e a rajada sobe o risco até o 2FA:

            1º  noite 20 + destino 5                       = 25  aprovado
            2º  25 + herança 12                            = 37  aprovado
            3º  25 + velocidade 10 + herança 12            = 47  2FA
            4º  25 + velocidade 20 + herança 17            = 62  2FA
            5º e 6º  45 + herança 22                       = 67  2FA
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
        assert statuses == ["approved"] * 2 + ["two_factor_required"] * 4
