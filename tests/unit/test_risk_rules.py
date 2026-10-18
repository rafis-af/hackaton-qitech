from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from controllers import risk_rules

BRASILIA = timezone(timedelta(hours=-3))
DAYTIME = datetime(2026, 10, 14, 14, 0, tzinfo=BRASILIA)
NIGHTTIME = datetime(2026, 10, 14, 22, 15, tzinfo=BRASILIA)

SAO_PAULO = (-23.56, -46.65)
GUARULHOS = (-23.45, -46.53)
CAMPINAS = (-22.91, -47.06)
SALVADOR = (-12.97, -38.50)

METRICS = [
    SimpleNamespace(enumerator="hour", label="horário", max_points=35),
    SimpleNamespace(enumerator="amount", label="valor", max_points=30),
    SimpleNamespace(enumerator="location", label="local", max_points=25),
    SimpleNamespace(enumerator="velocity", label="velocidade", max_points=20),
    SimpleNamespace(enumerator="destination", label="destino", max_points=15),
    SimpleNamespace(enumerator="origin", label="origem", max_points=10),
]


def make_facts(**overrides) -> dict:
    facts = {
        "amount": 5000,
        "transaction_date": DAYTIME,
        "device_id": "dvc-1",
        "location": SAO_PAULO,
        "known_places": [SAO_PAULO],
        "payer": {"document": "12345678909", "document_is_valid": True, "restricted_reason": None},
        "destination": {
            "document": "98765432100",
            "document_is_valid": True,
            "opening_date": DAYTIME - timedelta(days=365),
            "restricted_reason": None,
            "receita_situation": "regular",
        },
        "history": {
            "trusted_count": 20,
            "average_ticket": 10000,
            "same_hour_band_count": 10,
            "known_destination": True,
            "burst_count": 0,
            "other_device_in_short_window": False,
        },
        "destination_payers": 1,
        "last_device_id": "dvc-1",
        "other_payers_on_device": 0,
        "other_payers_on_ip": 0,
    }

    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(facts.get(key), dict):
            facts[key] = {**facts[key], **value}
        else:
            facts[key] = value

    return facts


def rule(enumerator: str, metric: str = None, points: int = 0) -> SimpleNamespace:
    action = "score" if metric else "reject"
    risk_metric = SimpleNamespace(enumerator=metric) if metric else None

    return SimpleNamespace(
        enumerator=enumerator,
        risk_metric=risk_metric,
        action=action,
        points=points,
        description=f"descrição de {enumerator}",
    )


CATALOG = [
    rule("invalid_document_number"),
    rule("restricted_payer"),
    rule("restricted_destination"),
    rule("receita_destination_blocked"),
    rule("unusual_hour", "hour", 35),
    rule("rare_hour", "hour", 20),
    rule("night_without_history", "hour", 20),
    rule("amount_5x_profile", "amount", 30),
    rule("amount_3x_profile", "amount", 20),
    rule("amount_2x_profile", "amount", 10),
    rule("high_amount_without_history", "amount", 15),
    rule("far_from_usual_places", "location", 25),
    rule("burst_4_in_10_minutes", "velocity", 20),
    rule("burst_3_in_10_minutes", "velocity", 10),
    rule("new_destination", "destination", 5),
    rule("new_destination_account", "destination", 10),
    rule("destination_many_payers", "destination", 10),
    rule("receita_destination_irregular", "destination", 10),
    rule("device_changed", "origin", 5),
    rule("device_shared", "origin", 10),
    rule("ip_shared", "origin", 10),
]


def fires(check, **overrides) -> bool:
    return check(make_facts(**overrides)) is not None


def analyze(previous_analyses: list = None, **overrides) -> dict:
    signals = risk_rules.evaluate(CATALOG, make_facts(**overrides))
    metric_scores = risk_rules.score_metrics(signals, METRICS)
    inherited = risk_rules.inherit_risk(previous_analyses or [])

    return risk_rules.decide(signals, metric_scores, inherited["points"])


class TestTheRulesOfTheGame:
    def test_no_metric_decides_alone(self):
        for metric in METRICS:
            assert metric.max_points < risk_rules.TWO_FACTOR_FROM, metric.enumerator

    def test_hour_weighs_more_than_location(self):
        ceilings = {metric.enumerator: metric.max_points for metric in METRICS}
        assert ceilings["hour"] > ceilings["location"]

    def test_quiet_pix_has_no_risk(self):
        decision = analyze()

        assert decision["risk_score"] == 0
        assert decision["status"] == "approved"

    def test_two_strong_metrics_ask_two_factor_and_three_reject(self):
        two = analyze(history={"same_hour_band_count": 0}, location=SALVADOR)
        three = analyze(history={"same_hour_band_count": 0}, location=SALVADOR, amount=60000)

        assert two["transaction_risk"] == 35 + 25
        assert two["status"] == "two_factor_required"
        assert three["transaction_risk"] == 35 + 25 + 30
        assert three["status"] == "rejected"


class TestHour:
    def test_never_paid_at_this_hour(self):
        assert fires(risk_rules.check_unusual_hour, history={"same_hour_band_count": 0})
        assert not fires(risk_rules.check_unusual_hour)

    def test_rare_hour_is_under_ten_percent(self):
        assert fires(risk_rules.check_rare_hour, history={"same_hour_band_count": 1})
        assert not fires(risk_rules.check_rare_hour, history={"same_hour_band_count": 2})

    def test_without_history_night_is_the_reference(self):
        no_history = {"trusted_count": 2, "same_hour_band_count": 0}

        assert fires(risk_rules.check_night_without_history, history=no_history, transaction_date=NIGHTTIME)
        assert not fires(risk_rules.check_night_without_history, history=no_history, transaction_date=DAYTIME)
        assert not fires(risk_rules.check_unusual_hour, history=no_history)
        assert not fires(risk_rules.check_night_without_history, transaction_date=NIGHTTIME)

    def test_night_worker_is_not_punished_for_the_night(self):
        decision = analyze(transaction_date=DAYTIME.replace(hour=3), history={"same_hour_band_count": 15})
        assert decision["risk_score"] == 0

    def test_hour_band_wraps_around_midnight(self):
        assert risk_rules.hour_band(0) == [0, 1, 23]
        assert risk_rules.hour_band(14) == [13, 14, 15]


class TestAmount:
    def test_ratio_levels_against_the_average_ticket(self):
        assert fires(risk_rules.check_amount_5x_profile, amount=50000)
        assert fires(risk_rules.check_amount_3x_profile, amount=30000)
        assert not fires(risk_rules.check_amount_3x_profile, amount=50000)
        assert fires(risk_rules.check_amount_2x_profile, amount=20000)
        assert not fires(risk_rules.check_amount_2x_profile, amount=19999)

    def test_without_ticket_a_thousand_reais_is_the_reference(self):
        no_history = {"trusted_count": 2, "average_ticket": 5000}

        assert fires(risk_rules.check_high_amount_without_history, history=no_history, amount=100001)
        assert not fires(risk_rules.check_high_amount_without_history, history=no_history, amount=100000)
        assert not fires(risk_rules.check_amount_5x_profile, history=no_history, amount=900000)

    def test_rent_alone_does_not_decide(self):
        decision = analyze(amount=500000)

        assert decision["transaction_risk"] == 30
        assert decision["status"] == "approved"


class TestLocation:
    def test_distance_is_in_km(self):
        assert 15 < risk_rules.distance_km(SAO_PAULO, GUARULHOS) < 20
        assert 80 < risk_rules.distance_km(SAO_PAULO, CAMPINAS) < 90
        assert 1400 < risk_rules.distance_km(SAO_PAULO, SALVADOR) < 1500

    def test_far_from_every_known_place(self):
        assert fires(risk_rules.check_far_from_usual_places, location=SALVADOR)
        assert not fires(risk_rules.check_far_from_usual_places, location=GUARULHOS)

    def test_measures_to_the_nearest_place_not_to_the_average(self):
        known_places = [SAO_PAULO, CAMPINAS]

        assert not fires(risk_rules.check_far_from_usual_places, location=SAO_PAULO, known_places=known_places)
        assert not fires(risk_rules.check_far_from_usual_places, location=CAMPINAS, known_places=known_places)

    def test_needs_something_to_compare(self):
        assert not fires(risk_rules.check_far_from_usual_places, location=None)
        assert not fires(risk_rules.check_far_from_usual_places, location=SALVADOR, known_places=[])

    def test_the_traveler_is_not_stopped(self):
        decision = analyze(location=SALVADOR)

        assert decision["transaction_risk"] == 25
        assert decision["status"] == "approved"


class TestVelocity:
    def test_this_pix_counts_too(self):
        assert fires(risk_rules.check_burst_3_in_10_minutes, history={"burst_count": 2})
        assert not fires(risk_rules.check_burst_4_in_10_minutes, history={"burst_count": 2})
        assert fires(risk_rules.check_burst_4_in_10_minutes, history={"burst_count": 3})
        assert not fires(risk_rules.check_burst_3_in_10_minutes, history={"burst_count": 3})
        assert not fires(risk_rules.check_burst_3_in_10_minutes, history={"burst_count": 1})


class TestDestinationAndOrigin:
    def test_destination_signals(self):
        assert fires(risk_rules.check_new_destination, history={"known_destination": False})
        assert fires(risk_rules.check_new_destination_account, destination={"opening_date": DAYTIME - timedelta(days=29)})
        assert not fires(risk_rules.check_new_destination_account, destination={"opening_date": DAYTIME - timedelta(days=30)})
        assert fires(risk_rules.check_destination_many_payers, destination_payers=5)
        assert not fires(risk_rules.check_destination_many_payers, destination_payers=4)
        assert fires(risk_rules.check_receita_destination_irregular, destination={"receita_situation": "suspended"})

    def test_destination_metric_stops_at_its_ceiling(self):
        decision = analyze(
            history={"known_destination": False},
            destination={"opening_date": DAYTIME - timedelta(days=3)},
            destination_payers=6,
        )

        assert decision["transaction_risk"] == 15

    def test_origin_signals(self):
        assert fires(risk_rules.check_device_changed, last_device_id="dvc-antigo")
        assert fires(risk_rules.check_device_changed, history={"other_device_in_short_window": True})
        assert not fires(risk_rules.check_device_changed, last_device_id=None)
        assert fires(risk_rules.check_device_shared, other_payers_on_device=1)
        assert fires(risk_rules.check_ip_shared, other_payers_on_ip=1)


class TestFacts:
    def test_facts_reject_directly(self):
        cases = [
            {"payer": {"document_is_valid": False}},
            {"payer": {"restricted_reason": "sanctions"}},
            {"destination": {"restricted_reason": "confirmed_fraud"}},
            {"destination": {"receita_situation": "deceased"}},
        ]

        for overrides in cases:
            decision = analyze(**overrides)

            assert decision["status"] == "rejected", overrides
            assert decision["reason"] == "blocking_rule", overrides

    def test_receita_down_never_rejects(self):
        assert not fires(risk_rules.check_receita_destination_blocked, destination={"receita_situation": None})
        assert not fires(risk_rules.check_receita_destination_irregular, destination={"receita_situation": None})


class TestMemory:
    def previous(self, risk: int, two_factor_result: str = None, pix_id: str = "pix-anterior") -> dict:
        return {"pix_id": pix_id, "transaction_risk": risk, "two_factor_result": two_factor_result}

    def test_carried_fraction_by_two_factor_outcome(self):
        assert risk_rules.inherit_risk([self.previous(50, "confirmed")])["points"] == 0
        assert risk_rules.inherit_risk([self.previous(50, "failed")])["points"] == 50
        assert risk_rules.inherit_risk([self.previous(50, "pending")])["points"] == 25
        assert risk_rules.inherit_risk([self.previous(50)])["points"] == 25

    def test_the_largest_valid_risk_wins(self):
        inherited = risk_rules.inherit_risk(
            [self.previous(30, pix_id="a"), self.previous(60, pix_id="b"), self.previous(90, "confirmed", pix_id="c")]
        )

        assert inherited == {"points": 30, "from_pix_id": "b", "carried_fraction": 0.5}

    def test_the_insistent_thief_is_rejected(self):
        decision = analyze(
            [self.previous(50, "failed")],
            history={"same_hour_band_count": 0},
        )

        assert decision["transaction_risk"] == 35
        assert decision["inherited_risk"] == 50
        assert decision["risk_score"] == 85
        assert decision["status"] == "rejected"

    def test_score_is_capped_at_one_hundred(self):
        decision = analyze(
            [self.previous(100, "failed")],
            history={"same_hour_band_count": 0},
        )

        assert decision["risk_score"] == 100


class TestChallengeExample:
    def test_challenge_example_asks_two_factor(self):
        decision = analyze(
            amount=150000,
            transaction_date=datetime(2026, 10, 17, 22, 15, tzinfo=BRASILIA),
            destination={"opening_date": datetime(2026, 10, 10, 9, 0, tzinfo=BRASILIA)},
            history={
                "trusted_count": 0,
                "average_ticket": None,
                "same_hour_band_count": 0,
                "known_destination": False,
            },
            known_places=[],
            last_device_id=None,
        )

        assert decision["transaction_risk"] == 50
        assert decision["status"] == "two_factor_required"
        assert decision["decisive_rules"] == [
            "night_without_history",
            "high_amount_without_history",
            "new_destination",
            "new_destination_account",
        ]
