from datetime import timedelta

from tests.utils import (
    DAYTIME,
    GUARULHOS,
    NIGHTTIME,
    SALVADOR,
    ObjectGenerator,
    PayloadGenerator,
    RandomGenerator,
    RequestGenerator,
)

RESTRICTED_DOCUMENT = "529.982.247-25"


def build_history(payer: str, destination: dict, days: int, amount: int = 10000) -> None:
    for days_ago in range(days, 0, -1):
        ObjectGenerator.send_pix(
            payer_document=payer,
            destination=destination,
            amount=amount,
            transaction_date=DAYTIME - timedelta(days=days_ago),
        )


class TestFacts:
    def test_invalid_document_is_rejected_and_recorded(self):
        destination = PayloadGenerator.create_destination(document_number="111.222.333-44")

        analysis = ObjectGenerator.send_pix(destination=destination)

        assert analysis["status"] == "rejected"
        assert ObjectGenerator.decision(analysis)["reason"] == "blocking_rule"
        assert ObjectGenerator.decision(analysis)["rules"] == ["invalid_document_number"]
        assert analysis["metadata"]["receita"]["source"] == "skipped"

    def test_restricted_payer_is_rejected(self):
        analysis = ObjectGenerator.send_pix(payer_document=RESTRICTED_DOCUMENT)

        assert analysis["status"] == "rejected"
        assert ObjectGenerator.decision(analysis)["rules"] == ["restricted_payer"]

    def test_rejected_pix_is_recorded_with_the_reason(self):
        destination = PayloadGenerator.create_destination(document_number=RESTRICTED_DOCUMENT)

        analysis = ObjectGenerator.send_pix(destination=destination)
        status, saved = RequestGenerator.GET_pix_transaction(analysis["pix_id"])

        assert status == 200
        assert saved["status"] == "rejected"
        assert ObjectGenerator.decision(saved) == {
            "reason": "blocking_rule",
            "rules": ["restricted_destination"],
            "description": "Reprovado por um fato: Quem recebe está na lista restritiva",
        }


class TestReceita:
    def send_to(self, prefix: str, **payload_fields) -> dict:
        destination = PayloadGenerator.create_destination(document_number=RandomGenerator.generate_cpf(prefix))
        return ObjectGenerator.send_pix(destination=destination, **payload_fields)

    def test_deceased_or_inexistent_receiver_is_rejected(self):
        for prefix, situation in [("001", "deceased"), ("002", "canceled"), ("003", "nullified"), ("000", "not_found")]:
            analysis = self.send_to(prefix)

            assert analysis["status"] == "rejected", situation
            assert ObjectGenerator.decision(analysis)["rules"] == ["receita_destination_blocked"], situation
            assert analysis["metadata"]["receita"] == {"source": "api", "situation": situation}

    def test_suspended_receiver_only_weighs(self):
        analysis = self.send_to("004")

        assert analysis["status"] == "approved"
        assert "receita_destination_irregular" in ObjectGenerator.fired_rules(analysis)
        assert ObjectGenerator.metric_points(analysis)["destination"] == 15

    def test_receita_down_never_rejects(self):
        for prefix, error in [("006", "timeout"), ("007", "http_500"), ("008", "malformed_response")]:
            analysis = self.send_to(prefix)

            assert analysis["status"] == "approved", error
            assert analysis["metadata"]["receita"] == {"source": "unavailable", "situation": None, "error": error}

    def test_second_pix_to_the_same_receiver_uses_the_cache(self):
        destination = PayloadGenerator.create_destination()

        first = ObjectGenerator.send_pix(destination=destination)
        second = ObjectGenerator.send_pix(destination=destination, transaction_date=DAYTIME + timedelta(minutes=30))

        assert first["metadata"]["receita"]["source"] == "api"
        assert second["metadata"]["receita"] == {"source": "cache", "situation": "regular"}


class TestHour:
    def test_unusual_hour_alone_does_not_decide(self):
        payer = RandomGenerator.generate_cpf()
        rent = PayloadGenerator.create_destination()
        build_history(payer, rent, days=5)

        at_three_am = ObjectGenerator.send_pix(
            payer_document=payer, destination=rent, transaction_date=DAYTIME.replace(hour=3)
        )

        assert "unusual_hour" in ObjectGenerator.fired_rules(at_three_am)
        assert ObjectGenerator.metric_points(at_three_am)["hour"] == 35
        assert at_three_am["status"] == "approved"

    def test_unusual_hour_plus_a_new_account_asks_two_factor(self):
        payer = RandomGenerator.generate_cpf()
        build_history(payer, PayloadGenerator.create_destination(), days=5)
        brand_new_account = PayloadGenerator.create_destination(opening_date=DAYTIME - timedelta(days=3))

        analysis = ObjectGenerator.send_pix(
            payer_document=payer, destination=brand_new_account, transaction_date=DAYTIME.replace(hour=3)
        )

        assert ObjectGenerator.risk(analysis)["transaction_risk"] == 50
        assert analysis["status"] == "two_factor_required"


class TestStreetMode:
    def test_far_alone_does_not_stop_the_traveler(self):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_pix(payer_document=payer)
        in_salvador = ObjectGenerator.send_pix(
            payer_document=payer, geolocation=SALVADOR, transaction_date=DAYTIME + timedelta(hours=3)
        )

        assert "far_from_usual_places" in ObjectGenerator.fired_rules(in_salvador)
        assert ObjectGenerator.metric_points(in_salvador)["location"] == 25
        assert in_salvador["status"] == "approved"

    def test_far_and_at_night_asks_two_factor(self):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_pix(payer_document=payer)
        far_at_night = ObjectGenerator.send_pix(payer_document=payer, geolocation=SALVADOR, transaction_date=NIGHTTIME)

        assert ObjectGenerator.metric_points(far_at_night)["hour"] == 20
        assert ObjectGenerator.metric_points(far_at_night)["location"] == 25
        assert far_at_night["status"] == "two_factor_required"

    def test_inside_the_radius_changes_nothing(self):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_pix(payer_document=payer)
        in_guarulhos = ObjectGenerator.send_pix(
            payer_document=payer, geolocation=GUARULHOS, transaction_date=DAYTIME + timedelta(hours=1)
        )

        assert "far_from_usual_places" not in ObjectGenerator.fired_rules(in_guarulhos)

    def test_without_geolocation_there_is_nothing_to_compare(self):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_pix(payer_document=payer)
        no_location = ObjectGenerator.send_pix(
            payer_document=payer, geolocation=None, transaction_date=DAYTIME + timedelta(hours=1)
        )

        assert "far_from_usual_places" not in ObjectGenerator.fired_rules(no_location)

    def test_the_traveler_confirms_once_and_the_city_becomes_usual(self):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_pix(payer_document=payer)
        first_in_salvador = ObjectGenerator.send_pix(payer_document=payer, geolocation=SALVADOR, transaction_date=NIGHTTIME)
        RequestGenerator.POST_two_factor(first_in_salvador["pix_id"], {"result": "confirmed"})

        next_day = ObjectGenerator.send_pix(
            payer_document=payer, geolocation=SALVADOR, transaction_date=NIGHTTIME + timedelta(hours=14)
        )

        assert first_in_salvador["status"] == "two_factor_required"
        assert "far_from_usual_places" not in ObjectGenerator.fired_rules(next_day)

    def test_unconfirmed_pix_never_teaches_a_place(self):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_pix(payer_document=payer)
        ObjectGenerator.send_pix(payer_document=payer, geolocation=SALVADOR, transaction_date=NIGHTTIME)
        again_in_salvador = ObjectGenerator.send_pix(
            payer_document=payer, geolocation=SALVADOR, transaction_date=NIGHTTIME + timedelta(hours=1)
        )

        assert "far_from_usual_places" in ObjectGenerator.fired_rules(again_in_salvador)


class TestAmountAndDestination:
    def test_high_amount_to_known_destination_is_approved(self):
        payer = RandomGenerator.generate_cpf()
        rent = PayloadGenerator.create_destination()
        build_history(payer, rent, days=3, amount=50000)

        to_rent = ObjectGenerator.send_pix(payer_document=payer, destination=rent, amount=500000)

        assert ObjectGenerator.metric_points(to_rent)["amount"] == 30
        assert to_rent["status"] == "approved"

    def test_high_amount_to_a_brand_new_account_asks_two_factor(self):
        payer = RandomGenerator.generate_cpf()
        build_history(payer, PayloadGenerator.create_destination(), days=3, amount=50000)
        brand_new_account = PayloadGenerator.create_destination(opening_date=DAYTIME - timedelta(days=5))

        analysis = ObjectGenerator.send_pix(payer_document=payer, destination=brand_new_account, amount=500000)

        assert ObjectGenerator.risk(analysis)["transaction_risk"] == 45
        assert analysis["status"] == "two_factor_required"

    def test_destination_receiving_from_many_payers(self):
        destination = PayloadGenerator.create_destination()

        analyses = []
        for minute in range(5):
            analyses.append(
                ObjectGenerator.send_pix(destination=destination, transaction_date=DAYTIME + timedelta(minutes=minute))
            )

        assert "destination_many_payers" not in ObjectGenerator.fired_rules(analyses[3])
        assert "destination_many_payers" in ObjectGenerator.fired_rules(analyses[4])


class TestLimitAndDaySum:
    def test_night_pix_above_the_limit_weighs_even_inside_the_ticket(self):
        payer = RandomGenerator.generate_cpf()
        rent = PayloadGenerator.create_destination()
        build_history(payer, rent, days=5, amount=80000)

        at_night = ObjectGenerator.send_pix(payer_document=payer, destination=rent, amount=150000, transaction_date=NIGHTTIME)

        assert "night_limit_exceeded" in ObjectGenerator.fired_rules(at_night)
        assert "amount_2x_profile" not in ObjectGenerator.fired_rules(at_night)
        assert ObjectGenerator.metric_points(at_night)["amount"] == 20

    def test_split_pix_adds_up(self):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()

        first = ObjectGenerator.send_pix(payer_document=payer, destination=destination, amount=60000)
        second = ObjectGenerator.send_pix(
            payer_document=payer, destination=destination, amount=60000, transaction_date=DAYTIME + timedelta(hours=1)
        )

        assert "day_sum_without_history" not in ObjectGenerator.fired_rules(first)
        assert "day_sum_without_history" in ObjectGenerator.fired_rules(second)
        assert "high_amount_without_history" not in ObjectGenerator.fired_rules(second)
        assert ObjectGenerator.metric_points(second)["amount"] == 15


class TestVelocity:
    def test_burst_of_small_pix(self):
        payer = RandomGenerator.generate_cpf()
        destination = PayloadGenerator.create_destination()

        analyses = []
        for minute in range(4):
            analyses.append(
                ObjectGenerator.send_pix(
                    payer_document=payer, destination=destination, transaction_date=DAYTIME + timedelta(minutes=minute)
                )
            )

        assert "burst_3_in_10_minutes" in ObjectGenerator.fired_rules(analyses[2])
        assert "burst_4_in_10_minutes" in ObjectGenerator.fired_rules(analyses[3])
        assert ObjectGenerator.metric_points(analyses[3])["velocity"] == 20


class TestOrigin:
    def test_device_change_weighs_for_the_next_hours(self):
        payer = RandomGenerator.generate_cpf()
        old_phone = f"antigo-{payer}"
        new_phone = f"novo-{payer}"

        ObjectGenerator.send_pix(payer_document=payer, device_id=old_phone)
        swapped = ObjectGenerator.send_pix(
            payer_document=payer, device_id=new_phone, transaction_date=DAYTIME + timedelta(hours=1)
        )
        same_day = ObjectGenerator.send_pix(
            payer_document=payer, device_id=new_phone, transaction_date=DAYTIME + timedelta(hours=2)
        )
        two_days_later = ObjectGenerator.send_pix(
            payer_document=payer, device_id=new_phone, transaction_date=DAYTIME + timedelta(days=2)
        )

        assert "device_changed" in ObjectGenerator.fired_rules(swapped)
        assert "device_changed" in ObjectGenerator.fired_rules(same_day)
        assert "device_changed" not in ObjectGenerator.fired_rules(two_days_later)

    def test_same_device_or_ip_on_another_account(self):
        device_id = f"celular-{RandomGenerator.generate_cpf()}"
        ip = "203.0.113.77"

        ObjectGenerator.send_pix(device_id=device_id, ip=ip)
        second_account = ObjectGenerator.send_pix(
            device_id=device_id, ip=ip, transaction_date=DAYTIME + timedelta(hours=1)
        )

        assert "device_shared" in ObjectGenerator.fired_rules(second_account)
        assert "ip_shared" in ObjectGenerator.fired_rules(second_account)
        assert ObjectGenerator.metric_points(second_account)["origin"] == 10
        assert second_account["status"] == "approved"


class TestMemory:
    def test_unanswered_risk_passes_half_to_the_next_pix(self):
        payer = RandomGenerator.generate_cpf()

        first = ObjectGenerator.send_two_factor_pix(payer_document=payer)
        next_pix = ObjectGenerator.send_pix(payer_document=payer, transaction_date=NIGHTTIME + timedelta(hours=1))

        assert ObjectGenerator.risk(next_pix)["inherited_risk"] == 25
        assert ObjectGenerator.risk(next_pix)["inherited_from_pix_id"] == first["pix_id"]

    def test_the_insistent_thief_is_rejected(self):
        payer = RandomGenerator.generate_cpf()

        first = ObjectGenerator.send_two_factor_pix(payer_document=payer)
        RequestGenerator.POST_two_factor(first["pix_id"], {"result": "failed"})

        retry = ObjectGenerator.send_pix(
            payer_document=payer,
            amount=150000,
            transaction_date=NIGHTTIME + timedelta(minutes=5),
            destination=PayloadGenerator.create_destination(opening_date=NIGHTTIME - timedelta(days=7)),
        )

        assert ObjectGenerator.risk(retry)["inherited_risk"] == 50
        assert retry["status"] == "rejected"

    def test_confirmed_two_factor_leaves_no_risk_behind(self):
        payer = RandomGenerator.generate_cpf()

        first = ObjectGenerator.send_two_factor_pix(payer_document=payer)
        RequestGenerator.POST_two_factor(first["pix_id"], {"result": "confirmed"})

        next_pix = ObjectGenerator.send_pix(payer_document=payer, transaction_date=NIGHTTIME + timedelta(hours=1))

        assert ObjectGenerator.risk(next_pix)["inherited_risk"] == 0

    def test_risk_expires_after_24_hours(self):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_two_factor_pix(payer_document=payer)
        a_day_later = ObjectGenerator.send_pix(payer_document=payer, transaction_date=NIGHTTIME + timedelta(hours=25))

        assert ObjectGenerator.risk(a_day_later)["inherited_risk"] == 0
