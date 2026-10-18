import uuid

from tests.utils import (
    INTERNAL_TOKEN,
    DbUtils,
    ObjectGenerator,
    PayloadGenerator,
    RandomGenerator,
    RequestGenerator,
)

RESTRICTED_DOCUMENT = "529.982.247-25"


def timeline(pix_id: str) -> list:
    status, saved = RequestGenerator.GET_pix_transaction(pix_id)
    assert status == 200, saved
    return saved["metadata"]["events"]


class TestPixEvents:
    def test_new_pix_starts_the_timeline(self):
        analysis = ObjectGenerator.send_pix()

        [created] = analysis["metadata"]["events"]

        assert created["type"] == "analysis_created"
        assert created["error_code"] is None
        assert created["details"] == {
            "status": analysis["status"],
            "risk_score": analysis["metadata"]["risk_score"],
            "decision_reason": "risk_score",
        }

    def test_rejected_pix_is_also_on_the_timeline(self):
        restricted = PayloadGenerator.create_destination(document_number=RESTRICTED_DOCUMENT)

        analysis = ObjectGenerator.send_pix(destination=restricted)

        assert ObjectGenerator.event_types(analysis) == ["analysis_created"]
        assert analysis["metadata"]["events"][0]["details"]["decision_reason"] == "blocking_rule"

    def test_receita_down_is_recorded_before_the_decision(self):
        destination = PayloadGenerator.create_destination(document_number=RandomGenerator.generate_cpf("007"))

        analysis = ObjectGenerator.send_pix(destination=destination)

        assert ObjectGenerator.event_types(analysis) == ["receita_unavailable", "analysis_created"]
        assert analysis["metadata"]["events"][0]["details"] == {"reason": "http_500"}

    def test_request_id_links_the_event_to_the_application_log(self):
        payload = PayloadGenerator.create_pix_payload()
        request_id = f"teste-{uuid.uuid4().hex[:12]}"

        status, analysis = RequestGenerator.POST_pix_transaction(
            payload, headers={"INTERNAL-TOKEN": INTERNAL_TOKEN, "X-Request-ID": request_id}
        )

        assert status == 201
        assert analysis["metadata"]["events"][0]["request_id"] == request_id

    def test_resend_adds_a_replay_event(self):
        payload = PayloadGenerator.create_pix_payload()

        first_status, _first = RequestGenerator.POST_pix_transaction(payload)
        second_status, _second = RequestGenerator.POST_pix_transaction(payload)

        assert (first_status, second_status) == (201, 200)
        assert [event["type"] for event in timeline(payload["id"])] == ["analysis_created", "replay_returned"]

    def test_conflict_is_refused_and_recorded(self):
        payload = PayloadGenerator.create_pix_payload()
        RequestGenerator.POST_pix_transaction(payload)

        status, response = RequestGenerator.POST_pix_transaction(dict(payload, amount=payload["amount"] + 1))
        events = timeline(payload["id"])

        assert status == 409
        assert response["code"] == "QIT002001"
        assert events[-1]["type"] == "conflict_refused"
        assert events[-1]["error_code"] == "QIT002001"

    def test_two_factor_answer_is_recorded(self):
        pending = ObjectGenerator.send_two_factor_pix()

        status, answered = RequestGenerator.POST_two_factor(pending["pix_id"], {"result": "confirmed"})

        assert status == 201
        assert ObjectGenerator.event_types(answered) == ["analysis_created", "two_factor_answered"]
        assert answered["metadata"]["events"][-1]["details"] == {"result": "confirmed"}

    def test_second_answer_is_refused_and_recorded(self):
        pending = ObjectGenerator.send_two_factor_pix()
        RequestGenerator.POST_two_factor(pending["pix_id"], {"result": "confirmed"})

        status, response = RequestGenerator.POST_two_factor(pending["pix_id"], {"result": "failed"})
        last = timeline(pending["pix_id"])[-1]

        assert status == 409
        assert response["code"] == "QIT002004"
        assert last["type"] == "two_factor_refused"
        assert last["error_code"] == "QIT002004"
        assert last["details"] == {"reason": "already_answered", "result_sent": "failed"}

    def test_answer_to_a_pix_without_two_factor_is_refused_and_recorded(self):
        approved = ObjectGenerator.send_pix()

        status, _response = RequestGenerator.POST_two_factor(approved["pix_id"], {"result": "confirmed"})
        last = timeline(approved["pix_id"])[-1]

        assert status == 409
        assert last["type"] == "two_factor_refused"
        assert last["error_code"] == "QIT002003"
        assert last["details"] == {"reason": "not_required", "result_sent": "confirmed"}

    def test_late_answers_are_recorded_as_expired(self):
        pending = ObjectGenerator.send_two_factor_pix()
        DbUtils.expire_two_factor(pending["pix_id"])

        first_status, _first = RequestGenerator.POST_two_factor(pending["pix_id"], {"result": "confirmed"})
        second_status, _second = RequestGenerator.POST_two_factor(pending["pix_id"], {"result": "failed"})
        events = timeline(pending["pix_id"])

        assert (first_status, second_status) == (409, 409)
        assert [event["type"] for event in events] == ["analysis_created", "two_factor_expired", "two_factor_expired"]
        assert {event["error_code"] for event in events[1:]} == {"QIT002006"}
        assert [event["details"]["result_sent"] for event in events[1:]] == ["confirmed", "failed"]

    def test_reading_never_writes_on_the_timeline(self):
        analysis = ObjectGenerator.send_pix()

        first_read = timeline(analysis["pix_id"])
        second_read = timeline(analysis["pix_id"])

        assert first_read == second_read == analysis["metadata"]["events"]
