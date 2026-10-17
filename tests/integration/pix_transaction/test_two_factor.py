from datetime import timedelta

from tests.utils import DAYTIME, NIGHTTIME, ObjectGenerator, PayloadGenerator, RandomGenerator, RequestGenerator


class TestTwoFactor:
    """O banco avisa o que o cliente fez com o 2FA. A decisão do motor nunca muda."""

    def test_confirmed_two_factor_is_recorded_beside_the_decision(self):
        analysis = ObjectGenerator.send_pix(amount=50000)
        assert analysis["status"] == "two_factor_required"

        status, response = RequestGenerator.POST_two_factor(analysis["pix_id"], {"result": "confirmed"})

        assert status == 201
        assert response["status"] == "two_factor_required"
        assert response["metadata"]["two_factor"]["result"] == "confirmed"

        status, response = RequestGenerator.GET_pix_transaction(analysis["pix_id"])
        assert response["key"] == analysis["key"]
        assert response["metadata"]["two_factor"]["result"] == "confirmed"

    def test_answering_twice_is_409(self):
        analysis = ObjectGenerator.send_pix(amount=50000)
        RequestGenerator.POST_two_factor(analysis["pix_id"], {"result": "confirmed"})

        status, response = RequestGenerator.POST_two_factor(analysis["pix_id"], {"result": "failed"})

        assert status == 409
        assert response["code"] == "QIT002004"

    def test_pix_that_did_not_ask_two_factor_is_409(self):
        analysis = ObjectGenerator.send_pix(amount=5000)
        assert analysis["status"] == "approved"

        status, response = RequestGenerator.POST_two_factor(analysis["pix_id"], {"result": "confirmed"})

        assert status == 409
        assert response["code"] == "QIT002003"

    def test_unknown_pix_is_404(self):
        status, response = RequestGenerator.POST_two_factor("pix-que-nunca-chegou", {"result": "confirmed"})

        assert status == 404
        assert response["code"] == "QIT002002"

    def test_schema_refuses_other_results(self):
        analysis = ObjectGenerator.send_pix(amount=50000)

        status, response = RequestGenerator.POST_two_factor(analysis["pix_id"], {"result": "maybe"})

        assert status == 400
        assert response["code"] == "QIT000001"

    def test_failed_two_factor_raises_the_next_pix(self):
        """Celular roubado: o golpista tem a senha, mas não a biometria."""
        payer = RandomGenerator.generate_cpf()
        first = ObjectGenerator.send_pix(payer_document=payer, amount=50000)
        RequestGenerator.POST_two_factor(first["pix_id"], {"result": "failed"})

        next_pix = ObjectGenerator.send_pix(payer_document=payer, amount=5000, transaction_date=DAYTIME + timedelta(hours=1))

        assert "two_factor_failed_recently" in ObjectGenerator.fired_rules(next_pix)
        assert next_pix["status"] == "two_factor_required"

    def test_confirmed_two_factor_clears_the_doubt(self):
        """Pontos de um Pix com 2FA confirmado saem do score: a dúvida foi tirada pelo cliente."""
        payer = RandomGenerator.generate_cpf()
        new_account = PayloadGenerator.create_destination(opening_date=NIGHTTIME - timedelta(days=10))

        first = ObjectGenerator.send_pix(
            payer_document=payer, level="gold", destination=new_account, amount=150000, transaction_date=NIGHTTIME
        )
        assert first["metadata"]["risk_score"] == 3

        RequestGenerator.POST_two_factor(first["pix_id"], {"result": "confirmed"})

        second = ObjectGenerator.send_pix(
            payer_document=payer, level="gold", destination=new_account, amount=5000,
            transaction_date=NIGHTTIME + timedelta(hours=12),
        )

        assert second["metadata"]["score"]["previous_points"] == 0
        assert second["status"] == "approved"
