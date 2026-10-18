from datetime import datetime, timedelta

from tests.utils import NIGHTTIME, DbUtils, ObjectGenerator, RandomGenerator, RequestGenerator


class TestTwoFactor:
    def test_two_factor_is_born_pending_with_a_deadline(self):
        analysis = ObjectGenerator.send_two_factor_pix()
        approved = ObjectGenerator.send_pix(amount=5000)

        two_factor = analysis["metadata"]["two_factor"]
        received_at = datetime.fromisoformat(analysis["metadata"]["arrival"]["received_at"])

        assert two_factor["result"] == "pending"
        assert two_factor["resolved_at"] is None
        assert datetime.fromisoformat(two_factor["expires_at"]) - received_at == timedelta(minutes=3)

        status, saved = RequestGenerator.GET_pix_transaction(analysis["pix_id"])
        assert saved["metadata"]["two_factor"]["result"] == "pending"

        assert approved["status"] == "approved"
        assert "two_factor" not in approved["metadata"]

    def test_confirmed_two_factor_closes_the_pending(self):
        analysis = ObjectGenerator.send_two_factor_pix()

        status, response = RequestGenerator.POST_two_factor(analysis["pix_id"], {"result": "confirmed"})

        assert status == 201
        assert response["status"] == "two_factor_required"
        assert response["metadata"]["two_factor"]["result"] == "confirmed"
        assert response["metadata"]["two_factor"]["resolved_at"] is not None

        status, response = RequestGenerator.GET_pix_transaction(analysis["pix_id"])
        assert response["key"] == analysis["key"]
        assert response["metadata"]["two_factor"]["result"] == "confirmed"

    def test_answer_after_the_deadline_is_refused(self):
        analysis = ObjectGenerator.send_two_factor_pix()
        DbUtils.expire_two_factor(analysis["pix_id"])

        status, response = RequestGenerator.POST_two_factor(analysis["pix_id"], {"result": "confirmed"})

        assert status == 409
        assert response["code"] == "QIT002006"

        status, saved = RequestGenerator.GET_pix_transaction(analysis["pix_id"])
        assert saved["status"] == "two_factor_required"
        assert saved["metadata"]["two_factor"]["result"] == "expired"
        assert saved["metadata"]["two_factor"]["resolved_at"] is not None

        status, response = RequestGenerator.POST_two_factor(analysis["pix_id"], {"result": "confirmed"})
        assert status == 409
        assert response["code"] == "QIT002006"

    def test_answering_twice_is_409(self):
        analysis = ObjectGenerator.send_two_factor_pix()
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
        analysis = ObjectGenerator.send_two_factor_pix()

        status, response = RequestGenerator.POST_two_factor(analysis["pix_id"], {"result": "maybe"})

        assert status == 400
        assert response["code"] == "QIT000001"

    def test_failed_two_factor_passes_the_whole_risk_on(self):
        payer = RandomGenerator.generate_cpf()
        first = ObjectGenerator.send_two_factor_pix(payer_document=payer)
        RequestGenerator.POST_two_factor(first["pix_id"], {"result": "failed"})

        next_pix = ObjectGenerator.send_pix(payer_document=payer, transaction_date=NIGHTTIME + timedelta(hours=1))

        assert ObjectGenerator.risk(next_pix)["inherited_risk"] == 50
        assert ObjectGenerator.risk(next_pix)["inherited_fraction"] == 1.0
        assert next_pix["status"] == "rejected"

    def test_confirmed_two_factor_clears_the_doubt(self):
        payer = RandomGenerator.generate_cpf()
        first = ObjectGenerator.send_two_factor_pix(payer_document=payer)
        RequestGenerator.POST_two_factor(first["pix_id"], {"result": "confirmed"})

        next_pix = ObjectGenerator.send_pix(payer_document=payer, transaction_date=NIGHTTIME + timedelta(hours=12))

        assert ObjectGenerator.risk(next_pix)["inherited_risk"] == 0
        assert next_pix["status"] == "approved"
