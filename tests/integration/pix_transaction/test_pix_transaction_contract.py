from tests.utils import DbUtils, ObjectGenerator, PayloadGenerator, RequestGenerator


class TestPixTransactionContract:
    """O contrato do desafio: POST analisa e grava, GET devolve o que foi gravado."""

    def test_refuses_without_internal_token(self):
        payload = PayloadGenerator.create_pix_payload()

        status, response = RequestGenerator.POST_pix_transaction(payload, headers={})

        assert status == 403
        assert response["code"] == "QIT000002"

    def test_refuses_empty_body(self):
        status, response = RequestGenerator.POST_pix_transaction({})

        assert status == 400
        assert response["code"] == "QIT000001"

    def test_schema_refuses_unknown_field(self):
        payload = PayloadGenerator.create_pix_payload()
        payload["source"]["campo_que_nao_existe"] = 1

        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 400
        assert response["code"] == "QIT000001"

    def test_schema_refuses_malformed_fields(self):
        """Cada volta do laço entorta um campo e mantém os outros certos."""
        campos_tortos = [
            ("amount", 0),
            ("amount", "150000"),
            ("transaction_date", "17/10/2026 22:15"),
            ("transaction_date", "2026-10-17T22:15:00"),  # sem fuso: "22:15 de onde?"
        ]

        for campo, valor_torto in campos_tortos:
            payload = PayloadGenerator.create_pix_payload()
            payload[campo] = valor_torto

            status, response = RequestGenerator.POST_pix_transaction(payload)

            assert status == 400, f"o campo '{campo}' aceitou o valor '{valor_torto}'"
            assert response["code"] == "QIT000001"

    def test_refuses_impossible_date(self):
        payload = PayloadGenerator.create_pix_payload()
        payload["transaction_date"] = "2026-02-30T10:00:00-03:00"

        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 422
        assert response["code"] == "QIT002005"

    def test_small_daytime_pix_is_approved_without_friction(self):
        analysis = ObjectGenerator.send_pix(amount=5000)

        assert analysis["status"] == "approved"
        assert analysis["metadata"]["risk_score"] == 0
        assert analysis["metadata"]["signals"] == []
        assert len(analysis["key"]) == 36

    def test_challenge_example_asks_two_factor(self):
        """O JSON do desafio, como está. O banco nasce de novo antes, porque o
        pagador do exemplo é fixo e o histórico de uma rodada anterior mudaria o score.
        """
        DbUtils.rollback()
        payload = PayloadGenerator.create_challenge_payload()

        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 201
        assert response["status"] == "two_factor_required"
        assert response["metadata"]["risk_score"] == 3
        assert ObjectGenerator.fired_rules(response) == [
            "unknown_level_daily_limit",
            "payer_without_history",
            "night_limit",
            "new_destination_account",
        ]

    def test_get_returns_the_saved_analysis(self):
        analysis = ObjectGenerator.send_pix()

        status, response = RequestGenerator.GET_pix_transaction(analysis["pix_id"])

        assert status == 200
        assert response["key"] == analysis["key"]
        assert response["status"] == analysis["status"]
        assert response["metadata"] == analysis["metadata"]

    def test_get_unknown_pix_is_404(self):
        status, response = RequestGenerator.GET_pix_transaction("pix-que-nunca-chegou")

        assert status == 404
        assert response["code"] == "QIT002002"

    def test_resend_returns_the_original_analysis(self):
        """O banco não recebeu a resposta e mandou de novo: mesma análise, 200, sem rodar regra."""
        payload = PayloadGenerator.create_pix_payload(amount=50000)

        status, original = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201

        status, resent = RequestGenerator.POST_pix_transaction(payload)
        assert status == 200
        assert resent == original

    def test_same_id_with_other_data_is_409(self):
        payload = PayloadGenerator.create_pix_payload()
        status, _response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201

        payload["amount"] = payload["amount"] + 1
        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 409
        assert response["code"] == "QIT002001"
