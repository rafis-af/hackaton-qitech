import json
import random
import uuid
from datetime import timedelta

import pytest

from tests.utils import (
    DAYTIME,
    NIGHTTIME,
    ObjectGenerator,
    PayloadGenerator,
    RandomGenerator,
    RequestGenerator,
    to_iso,
)

RESTRICTED_DOCUMENT = "529.982.247-25"

AMOUNT_RULES = {
    "amount_2x_profile",
    "amount_3x_profile",
    "amount_5x_profile",
    "high_amount_without_history",
    "night_limit_exceeded",
    "day_sum_3x_profile",
    "day_sum_without_history",
}


def send_daily(payer: str, destination: dict, amounts: list, hour: int = 14) -> None:
    for days_ago, amount in zip(range(len(amounts), 0, -1), amounts):
        ObjectGenerator.send_pix(
            payer_document=payer,
            destination=destination,
            amount=amount,
            transaction_date=(DAYTIME - timedelta(days=days_ago)).replace(hour=hour),
        )


def signal(analysis: dict, rule: str) -> dict:
    return next(signal for signal in analysis["metadata"]["signals"] if signal["rule"] == rule)


def random_ip() -> str:
    return f"172.{random.randrange(16, 32)}.{random.randrange(256)}.{random.randrange(256)}"


def only_digits(document: str) -> str:
    return "".join(character for character in document if character.isdigit())


class TestTicketMedian:
    def test_one_outlier_does_not_make_the_client_immune(self):
        payer = RandomGenerator.generate_cpf()
        rent = PayloadGenerator.create_destination()
        send_daily(payer, rent, [1000000, 10000, 10000, 10000])

        six_times_the_ticket = ObjectGenerator.send_pix(payer_document=payer, destination=rent, amount=60000)

        assert "amount_5x_profile" in ObjectGenerator.fired_rules(six_times_the_ticket)
        assert signal(six_times_the_ticket, "amount_5x_profile")["observed"]["median_ticket"] == 10000
        assert six_times_the_ticket["status"] == "approved"

    def test_even_history_uses_the_middle_of_the_two_central_values(self):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()
        send_daily(payer, destination, [10000, 20000, 30000, 40000])

        analysis = ObjectGenerator.send_pix(payer_document=payer, destination=destination, amount=75000)

        observed = signal(analysis, "amount_3x_profile")["observed"]
        assert observed["median_ticket"] == 25000
        assert observed["ratio"] == 3.0

    @pytest.mark.parametrize(
        "amount, rule, points",
        [
            (19999, None, 0),
            (20000, "amount_2x_profile", 10),
            (29999, "amount_2x_profile", 10),
            (30000, "amount_3x_profile", 20),
            (49999, "amount_3x_profile", 20),
            (50000, "amount_5x_profile", 30),
        ],
    )
    def test_each_step_of_the_ticket(self, amount, rule, points):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()
        send_daily(payer, destination, [10000, 10000, 10000])

        analysis = ObjectGenerator.send_pix(payer_document=payer, destination=destination, amount=amount)

        expected = {rule} if rule else set()
        assert set(ObjectGenerator.fired_rules(analysis)) & AMOUNT_RULES == expected
        assert ObjectGenerator.metric_points(analysis)["amount"] == points

    def test_less_than_three_pix_is_not_a_ticket(self):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()
        send_daily(payer, destination, [10000, 10000])

        analysis = ObjectGenerator.send_pix(payer_document=payer, destination=destination, amount=90000)

        assert set(ObjectGenerator.fired_rules(analysis)) & AMOUNT_RULES == set()


class TestHourEdges:
    def test_rare_hour_weighs_less_than_a_never_used_hour(self):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()
        ObjectGenerator.send_pix(
            payer_document=payer, destination=destination, transaction_date=(DAYTIME - timedelta(days=11)).replace(hour=3)
        )
        send_daily(payer, destination, [10000] * 10)

        at_three_am = ObjectGenerator.send_pix(
            payer_document=payer, destination=destination, transaction_date=DAYTIME.replace(hour=3)
        )

        assert "rare_hour" in ObjectGenerator.fired_rules(at_three_am)
        assert "unusual_hour" not in ObjectGenerator.fired_rules(at_three_am)
        assert ObjectGenerator.metric_points(at_three_am)["hour"] == 20
        assert signal(at_three_am, "rare_hour")["observed"]["pix_in_band"] == 1

    @pytest.mark.parametrize("hour, fires", [(12, True), (13, False), (15, False), (16, True)])
    def test_one_hour_band_around_the_habit(self, hour, fires):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()
        send_daily(payer, destination, [10000] * 5)

        analysis = ObjectGenerator.send_pix(
            payer_document=payer, destination=destination, transaction_date=DAYTIME.replace(hour=hour)
        )

        assert ("unusual_hour" in ObjectGenerator.fired_rules(analysis)) is fires

    @pytest.mark.parametrize("hour, minute, fires", [(19, 59, False), (20, 0, True), (5, 59, True), (6, 0, False)])
    def test_night_window_for_clients_without_history(self, hour, minute, fires):
        analysis = ObjectGenerator.send_pix(transaction_date=DAYTIME.replace(hour=hour, minute=minute))

        assert ("night_without_history" in ObjectGenerator.fired_rules(analysis)) is fires


class TestNightLimitEdges:
    @pytest.mark.parametrize("amount, fires", [(100000, False), (100001, True)])
    def test_limit_is_strictly_above_one_thousand(self, amount, fires):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()
        send_daily(payer, destination, [80000] * 3)

        at_night = ObjectGenerator.send_pix(
            payer_document=payer, destination=destination, amount=amount, transaction_date=NIGHTTIME
        )

        assert ("night_limit_exceeded" in ObjectGenerator.fired_rules(at_night)) is fires

    def test_limit_is_only_for_individuals(self):
        company = RandomGenerator.generate_cnpj()
        destination = PayloadGenerator.create_destination()
        send_daily(company, destination, [80000] * 3)

        at_night = ObjectGenerator.send_pix(
            payer_document=company, destination=destination, amount=150000, transaction_date=NIGHTTIME
        )

        assert "night_limit_exceeded" not in ObjectGenerator.fired_rules(at_night)

    def test_client_who_already_paid_above_the_limit_at_night_is_not_punished(self):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()
        send_daily(payer, destination, [80000] * 3)

        first_night = ObjectGenerator.send_pix(
            payer_document=payer, destination=destination, amount=150000, transaction_date=NIGHTTIME - timedelta(days=1)
        )
        assert "night_limit_exceeded" in ObjectGenerator.fired_rules(first_night)
        assert first_night["status"] == "two_factor_required"
        RequestGenerator.POST_two_factor(first_night["pix_id"], {"result": "confirmed"})

        next_night = ObjectGenerator.send_pix(
            payer_document=payer, destination=destination, amount=150000, transaction_date=NIGHTTIME
        )

        assert "night_limit_exceeded" not in ObjectGenerator.fired_rules(next_night)


class TestDaySumEdges:
    def test_day_sum_three_times_the_daily_total(self):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()
        send_daily(payer, destination, [40000] * 3)

        ObjectGenerator.send_pix(payer_document=payer, destination=destination, amount=70000)
        second = ObjectGenerator.send_pix(
            payer_document=payer, destination=destination, amount=70000, transaction_date=DAYTIME + timedelta(hours=1)
        )

        observed = signal(second, "day_sum_3x_profile")["observed"]
        assert observed["day_sum"] == 140000
        assert observed["average_daily_total"] == 40000
        assert observed["ratio"] == 3.5
        assert ObjectGenerator.metric_points(second)["amount"] == 20

    def test_day_sum_at_the_floor_does_not_weigh(self):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_pix(payer_document=payer, amount=50000)
        second = ObjectGenerator.send_pix(payer_document=payer, amount=50000, transaction_date=DAYTIME + timedelta(hours=1))

        assert "day_sum_without_history" not in ObjectGenerator.fired_rules(second)

    def test_rejected_pix_do_not_add_to_the_sum(self):
        payer = RandomGenerator.generate_cpf()
        restricted = PayloadGenerator.create_destination(document_number=RESTRICTED_DOCUMENT)

        blocked = ObjectGenerator.send_pix(payer_document=payer, destination=restricted, amount=90000)
        second = ObjectGenerator.send_pix(payer_document=payer, amount=90000, transaction_date=DAYTIME + timedelta(hours=1))

        assert blocked["status"] == "rejected"
        assert "day_sum_without_history" not in ObjectGenerator.fired_rules(second)


class TestVelocityEdges:
    def test_rejected_attempts_count_in_the_burst(self):
        payer = RandomGenerator.generate_cpf()
        restricted = PayloadGenerator.create_destination(document_number=RESTRICTED_DOCUMENT)

        for minute in range(3):
            ObjectGenerator.send_pix(
                payer_document=payer, destination=restricted, transaction_date=DAYTIME + timedelta(minutes=minute)
            )
        fourth = ObjectGenerator.send_pix(payer_document=payer, transaction_date=DAYTIME + timedelta(minutes=3))

        assert "burst_4_in_10_minutes" in ObjectGenerator.fired_rules(fourth)
        assert signal(fourth, "burst_4_in_10_minutes")["observed"]["pix_count"] == 4

    def test_pix_older_than_ten_minutes_do_not_count(self):
        payer = RandomGenerator.generate_cpf()

        for minute in [0, 11]:
            ObjectGenerator.send_pix(payer_document=payer, transaction_date=DAYTIME + timedelta(minutes=minute))
        third = ObjectGenerator.send_pix(payer_document=payer, transaction_date=DAYTIME + timedelta(minutes=12))

        assert {"burst_3_in_10_minutes", "burst_4_in_10_minutes"} & set(ObjectGenerator.fired_rules(third)) == set()


class TestDestinationEdges:
    @pytest.mark.parametrize("days, fires", [(29, True), (30, False)])
    def test_new_account_is_younger_than_thirty_days(self, days, fires):
        destination = PayloadGenerator.create_destination(opening_date=DAYTIME - timedelta(days=days))

        analysis = ObjectGenerator.send_pix(destination=destination)

        assert ("new_destination_account" in ObjectGenerator.fired_rules(analysis)) is fires

    def test_pending_regularization_receiver_also_weighs(self):
        destination = PayloadGenerator.create_destination(document_number=RandomGenerator.generate_cpf("005"))

        analysis = ObjectGenerator.send_pix(destination=destination)

        assert analysis["status"] == "approved"
        assert signal(analysis, "receita_destination_irregular")["observed"] == {"situation": "pending_regularization"}

    def test_the_same_payer_many_times_is_not_many_payers(self):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()

        for minute in range(5):
            analysis = ObjectGenerator.send_pix(
                payer_document=payer, destination=destination, transaction_date=DAYTIME + timedelta(minutes=minute)
            )

        assert "destination_many_payers" not in ObjectGenerator.fired_rules(analysis)

    @pytest.mark.parametrize("result, still_new", [("confirmed", False), ("failed", True), (None, True)])
    def test_only_a_trusted_pix_makes_the_destination_known(self, result, still_new):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination(opening_date=NIGHTTIME - timedelta(days=7))

        first = ObjectGenerator.send_two_factor_pix(payer_document=payer, destination=destination)
        if result is not None:
            RequestGenerator.POST_two_factor(first["pix_id"], {"result": result})

        again = ObjectGenerator.send_pix(
            payer_document=payer, destination=destination, transaction_date=NIGHTTIME + timedelta(days=2)
        )

        assert ("new_destination" in ObjectGenerator.fired_rules(again)) is still_new


class TestOriginEdges:
    @pytest.mark.parametrize("days, fires", [(29, True), (31, False)])
    def test_shared_device_window_is_thirty_days(self, days, fires):
        device_id = f"celular-{uuid.uuid4().hex}"

        ObjectGenerator.send_pix(device_id=device_id, transaction_date=DAYTIME - timedelta(days=days))
        analysis = ObjectGenerator.send_pix(device_id=device_id)

        assert ("device_shared" in ObjectGenerator.fired_rules(analysis)) is fires

    @pytest.mark.parametrize("hours, fires", [(23, True), (25, False)])
    def test_shared_ip_window_is_twenty_four_hours(self, hours, fires):
        ip = random_ip()

        ObjectGenerator.send_pix(ip=ip, transaction_date=DAYTIME - timedelta(hours=hours))
        analysis = ObjectGenerator.send_pix(ip=ip)

        assert ("ip_shared" in ObjectGenerator.fired_rules(analysis)) is fires


class TestFactEdges:
    @pytest.mark.parametrize(
        "late_fields",
        [
            ["source_account.opening_date"],
            ["destination_account.opening_date"],
            ["source_account.opening_date", "destination_account.opening_date"],
        ],
    )
    def test_account_opened_after_the_pix_is_rejected_on_either_side(self, late_fields):
        payload = PayloadGenerator.create_pix_payload()
        for field in late_fields:
            account, _ = field.split(".")
            payload[account]["opening_date"] = to_iso(DAYTIME + timedelta(days=1))

        status, analysis = RequestGenerator.POST_pix_transaction(payload)

        assert status == 201
        assert analysis["status"] == "rejected"
        assert ObjectGenerator.decision(analysis)["rules"] == ["account_opened_after_pix"]
        assert signal(analysis, "account_opened_after_pix")["observed"] == {"opened_after_pix": late_fields}

    def test_invalid_payer_document_is_rejected(self):
        analysis = ObjectGenerator.send_pix(payer_document="123.456.789-00")

        assert analysis["status"] == "rejected"
        assert signal(analysis, "invalid_document_number")["observed"] == {
            "invalid_fields": ["source_account.owner.document_number"]
        }

    def test_valid_company_documents_are_accepted(self):
        destination = PayloadGenerator.create_destination(document_number=RandomGenerator.generate_cnpj())

        analysis = ObjectGenerator.send_pix(payer_document=RandomGenerator.generate_cnpj(), destination=destination)

        assert "invalid_document_number" not in ObjectGenerator.fired_rules(analysis)
        assert analysis["status"] == "approved"

    def test_a_fact_wins_over_any_score(self):
        risky_and_restricted = ObjectGenerator.send_pix(
            payer_document=RESTRICTED_DOCUMENT,
            amount=900000,
            transaction_date=NIGHTTIME,
            destination=PayloadGenerator.create_destination(opening_date=NIGHTTIME - timedelta(days=2)),
        )

        assert risky_and_restricted["status"] == "rejected"
        assert ObjectGenerator.decision(risky_and_restricted)["reason"] == "blocking_rule"
        assert ObjectGenerator.decision(risky_and_restricted)["rules"] == ["restricted_payer"]


class TestDecisionBands:
    def build_habit(self, payer: str, destination: dict) -> None:
        send_daily(payer, destination, [10000] * 5)

    def test_exactly_forty_asks_two_factor(self):
        payer = RandomGenerator.generate_cpf()
        self.build_habit(payer, PayloadGenerator.create_destination())

        analysis = ObjectGenerator.send_pix(payer_document=payer, amount=10000, transaction_date=DAYTIME.replace(hour=3))

        assert ObjectGenerator.risk(analysis)["transaction_risk"] == 40
        assert analysis["status"] == "two_factor_required"

    def test_two_full_drawers_never_reject(self):
        payer = RandomGenerator.generate_cpf()
        usual = PayloadGenerator.create_destination()
        self.build_habit(payer, usual)

        analysis = ObjectGenerator.send_pix(
            payer_document=payer, destination=usual, amount=50000, transaction_date=DAYTIME.replace(hour=3)
        )

        assert ObjectGenerator.metric_points(analysis)["hour"] == 35
        assert ObjectGenerator.metric_points(analysis)["amount"] == 30
        assert ObjectGenerator.risk(analysis)["transaction_risk"] == 65
        assert analysis["status"] == "two_factor_required"

    def test_exactly_seventy_rejects(self):
        payer = RandomGenerator.generate_cpf()
        self.build_habit(payer, PayloadGenerator.create_destination())

        analysis = ObjectGenerator.send_pix(payer_document=payer, amount=50000, transaction_date=DAYTIME.replace(hour=3))

        assert ObjectGenerator.risk(analysis)["transaction_risk"] == 70
        assert analysis["status"] == "rejected"
        assert ObjectGenerator.decision(analysis)["reason"] == "risk_score"


class TestResponseShape:
    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"amount": 150000, "transaction_date": NIGHTTIME, "destination_days": 7},
            {"destination_document": RESTRICTED_DOCUMENT},
        ],
    )
    def test_get_returns_exactly_what_post_answered(self, fields):
        fields = dict(fields)
        destination_days = fields.pop("destination_days", None)
        destination_document = fields.pop("destination_document", None)
        if destination_days or destination_document:
            fields["destination"] = PayloadGenerator.create_destination(
                opening_date=NIGHTTIME - timedelta(days=destination_days) if destination_days else None,
                document_number=destination_document,
            )

        posted = ObjectGenerator.send_pix(**fields)
        status, saved = RequestGenerator.GET_pix_transaction(posted.pop("pix_id"))

        assert status == 200
        assert saved == posted

    def test_response_has_only_key_status_and_metadata(self):
        analysis = ObjectGenerator.send_pix()
        analysis.pop("pix_id")

        assert set(analysis) == {"key", "status", "metadata"}
        assert str(uuid.UUID(analysis["key"])) == analysis["key"]
        assert analysis["status"] in ("approved", "two_factor_required", "rejected")

    def test_response_never_exposes_ip_device_or_documents(self):
        payer = RandomGenerator.generate_cpf()
        receiver = RandomGenerator.generate_cpf()
        device_id = f"dvc-segredo-{uuid.uuid4().hex}"
        ip = random_ip()
        destination = PayloadGenerator.create_destination(document_number=receiver)

        ObjectGenerator.send_pix(payer_document=payer, device_id=f"dvc-antigo-{uuid.uuid4().hex}")
        analysis = ObjectGenerator.send_pix(
            payer_document=payer,
            device_id=device_id,
            ip=ip,
            destination=destination,
            transaction_date=DAYTIME + timedelta(hours=1),
        )
        analysis.pop("pix_id")
        text = json.dumps(analysis, ensure_ascii=False)

        documents = [payer, receiver, only_digits(payer), only_digits(receiver)]
        for secret in [device_id, ip] + documents:
            assert secret not in text


class TestTwoFactorEdges:
    def test_rejected_pix_cannot_be_answered(self):
        restricted = PayloadGenerator.create_destination(document_number=RESTRICTED_DOCUMENT)
        rejected = ObjectGenerator.send_pix(destination=restricted)

        status, response = RequestGenerator.POST_two_factor(rejected["pix_id"], {"result": "confirmed"})

        assert status == 409
        assert response["code"] == "QIT002003"

    def test_failed_answer_closes_the_pending_and_keeps_the_status(self):
        pending = ObjectGenerator.send_two_factor_pix()

        status, answered = RequestGenerator.POST_two_factor(pending["pix_id"], {"result": "failed"})
        _, saved = RequestGenerator.GET_pix_transaction(pending["pix_id"])

        assert status == 201
        assert answered["status"] == "two_factor_required"
        assert answered["metadata"]["two_factor"]["result"] == "failed"
        assert answered["metadata"]["two_factor"]["resolved_at"] is not None
        assert saved == answered
