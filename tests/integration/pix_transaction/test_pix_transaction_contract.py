from datetime import datetime, timedelta, timezone

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
            # acima do BIGINT: sem o maximum do schema, isto virava 500
            ("amount", 99999999999999999999),
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

        # Pagador novo, de dia, valor pequeno: só o destino novo pesa (5).
        assert analysis["status"] == "approved"
        assert analysis["metadata"]["risk_score"] == 5
        assert ObjectGenerator.fired_rules(analysis) == ["new_destination"]
        assert len(analysis["key"]) == 36

    def test_challenge_example_asks_two_factor(self):
        """O JSON do desafio, como está. O banco nasce de novo antes, porque o
        pagador do exemplo é fixo e o histórico de uma rodada anterior mudaria o risco.

        noite sem histórico (20) + valor alto sem histórico (15) + destino
        novo e conta de 7 dias (15) = 50.
        """
        DbUtils.rollback()
        payload = PayloadGenerator.create_challenge_payload()

        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 201
        assert response["status"] == "two_factor_required"
        assert response["metadata"]["risk_score"] == 50
        assert ObjectGenerator.metric_points(response) == {
            "hour": 20,
            "amount": 15,
            "location": 0,
            "velocity": 0,
            "destination": 15,
            "origin": 0,
        }
        assert response["metadata"]["receita"] == {"source": "api", "situation": "regular"}
        assert ObjectGenerator.decision(response) == {
            "reason": "risk_score",
            "rules": [
                "night_without_history",
                "high_amount_without_history",
                "new_destination",
                "new_destination_account",
            ],
            "description": (
                "2FA pedido: risco 50 de 100 (50 deste Pix + 0 herdado das últimas 24h). "
                "Pesaram: horário 20, valor 15, destino 15"
            ),
        }

    def test_marks_transaction_date_far_from_arrival(self):
        """A data do payload longe da hora de chegada fica registrada, sem mudar a decisão."""
        now = datetime.now(timezone(timedelta(hours=-3)))

        on_time = ObjectGenerator.send_pix(transaction_date=now)
        old = ObjectGenerator.send_pix(transaction_date=now - timedelta(days=3))

        assert on_time["metadata"]["arrival"]["far_from_arrival"] is False
        assert old["metadata"]["arrival"]["far_from_arrival"] is True
        assert old["metadata"]["arrival"]["transaction_date_offset_minutes"] <= -3 * 24 * 60
        assert old["status"] == on_time["status"] == "approved"

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
