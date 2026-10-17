"""O motor sem banco: cada regra recebe os fatos prontos e só compara.

É por isso que as regras são funções puras — cada limiar é testado na
borda, com um dicionário na mão, em milissegundos.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from controllers import risk_rules

BRASILIA = timezone(timedelta(hours=-3))
DAYTIME = datetime(2026, 10, 14, 14, 0, tzinfo=BRASILIA)
NIGHTTIME = datetime(2026, 10, 14, 22, 15, tzinfo=BRASILIA)


def make_facts(**overrides) -> dict:
    """Um Pix que não dispara regra nenhuma; cada teste muda só o que importa."""
    facts = {
        "amount": 5000,
        "transaction_date": DAYTIME,
        "device_id": "dvc-1",
        "payer": {"document": "12345678909", "document_is_valid": True, "is_person": True, "level": "gold"},
        "destination": {
            "document": "98765432100",
            "document_is_valid": True,
            "opening_date": DAYTIME - timedelta(days=365),
            "restricted_reason": None,
        },
        "pix_key": None,
        "history": {
            "pix_count": 10,
            "accepted_count": 10,
            "average_ticket": 10000,
            "sum_today": 0,
            "sum_short_window": 0,
            "split_count": 0,
            "known_destination": True,
            "known_device": True,
            "failed_two_factor_count": 0,
        },
        "new_destinations": 0,
        "destination_payers": 1,
        "pix_key_other_accounts": 0,
    }

    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(facts.get(key), dict):
            facts[key] = {**facts[key], **value}
        else:
            facts[key] = value

    return facts


def rule(enumerator: str, action: str = "score", points: int = 1) -> SimpleNamespace:
    return SimpleNamespace(enumerator=enumerator, family="test", action=action, points=points)


def fires(check, **overrides) -> bool:
    return check(make_facts(**overrides)) is not None


class TestRulesAtTheEdge:
    def test_quiet_pix_fires_nothing(self):
        all_rules = [rule(name) for name in risk_rules.RULE_CHECKS]
        assert risk_rules.evaluate(all_rules, make_facts()) == []

    def test_night_limit_is_above_one_thousand_at_night_for_people_only(self):
        check = risk_rules.check_night_limit

        assert fires(check, transaction_date=NIGHTTIME, amount=100001)
        assert not fires(check, transaction_date=NIGHTTIME, amount=100000)
        assert not fires(check, transaction_date=DAYTIME, amount=500000)
        # 5:59 ainda é noite; 6:00 já é dia
        assert fires(check, transaction_date=DAYTIME.replace(hour=5, minute=59), amount=150000)
        assert not fires(check, transaction_date=DAYTIME.replace(hour=6, minute=0), amount=150000)
        # CNPJ: o limite noturno do Banco Central é de pessoa física
        assert not fires(check, transaction_date=NIGHTTIME, amount=150000, payer={"is_person": False})

    def test_silver_limit_rejects_only_explicit_silver(self):
        check = risk_rules.check_silver_daily_limit_exceeded

        assert fires(check, payer={"level": "silver"}, amount=60000, history={"sum_today": 50000})
        assert not fires(check, payer={"level": "silver"}, amount=50000, history={"sum_today": 50000})
        assert not fires(check, payer={"level": "gold"}, amount=900000)
        assert not fires(check, payer={"level": None}, amount=900000)

    def test_unknown_level_assumes_silver(self):
        check = risk_rules.check_unknown_level_daily_limit

        observed = check(make_facts(payer={"level": None}, amount=100001))
        assert observed["assumed_level"] == "silver"
        assert not fires(check, payer={"level": None}, amount=100000)
        assert not fires(check, payer={"level": "gold"}, amount=900000)

    def test_payer_without_history_pays_small_things_freely(self):
        check = risk_rules.check_payer_without_history
        no_history = {"pix_count": 0, "accepted_count": 0, "average_ticket": None}

        assert fires(check, history=no_history, amount=20001)
        assert not fires(check, history=no_history, amount=20000)
        assert not fires(check, amount=900000)

    def test_split_counts_this_pix_too(self):
        check = risk_rules.check_split_below_night_limit

        # dois anteriores + este = 3
        assert fires(check, transaction_date=NIGHTTIME, amount=90000, history={"split_count": 2})
        assert not fires(check, transaction_date=NIGHTTIME, amount=90000, history={"split_count": 1})
        # fora da faixa, ou de dia, não é fracionamento
        assert not fires(check, transaction_date=NIGHTTIME, amount=79999, history={"split_count": 5})
        assert not fires(check, transaction_date=DAYTIME, amount=90000, history={"split_count": 5})

    def test_high_amount_only_matters_for_a_new_destination(self):
        """A tese: valor alto para destino conhecido é o aluguel, não fraude."""
        check = risk_rules.check_amount_above_profile

        assert fires(check, amount=30001, history={"known_destination": False})
        assert not fires(check, amount=30001, history={"known_destination": True})
        assert not fires(check, amount=30000, history={"known_destination": False})
        # Sem padrão (menos de 3 Pix), não há com o que comparar
        assert not fires(check, amount=900000, history={"known_destination": False, "accepted_count": 2})

    def test_daily_sum_has_a_floor(self):
        check = risk_rules.check_daily_sum_above_profile

        # ticket de R$ 100: 5x seria R$ 500, mas o piso é R$ 1.000
        assert not fires(check, amount=60000, history={"sum_short_window": 30000})
        assert fires(check, amount=60000, history={"sum_short_window": 40001})

    def test_new_device_needs_history(self):
        check = risk_rules.check_new_device

        assert fires(check, history={"known_device": False})
        assert not fires(check, history={"known_device": False, "pix_count": 0})

    def test_new_destination_account_is_under_thirty_days(self):
        check = risk_rules.check_new_destination_account

        assert fires(check, destination={"opening_date": DAYTIME - timedelta(days=29, hours=23)})
        assert not fires(check, destination={"opening_date": DAYTIME - timedelta(days=30)})

    def test_counterparty_thresholds(self):
        assert fires(risk_rules.check_many_new_destinations, new_destinations=5)
        assert not fires(risk_rules.check_many_new_destinations, new_destinations=4)
        assert fires(risk_rules.check_destination_many_payers, destination_payers=5)
        assert not fires(risk_rules.check_destination_many_payers, destination_payers=4)
        assert fires(risk_rules.check_pix_key_moved_account, pix_key_other_accounts=1)
        assert fires(risk_rules.check_restricted_destination, destination={"restricted_reason": "confirmed_fraud"})
        assert fires(risk_rules.check_two_factor_failed_recently, history={"failed_two_factor_count": 1})

    def test_pix_key_rules(self):
        new_key = {"type": "evp", "belongs_to_destination_owner": None, "created_at": DAYTIME - timedelta(hours=2)}
        old_key = {"type": "evp", "belongs_to_destination_owner": None, "created_at": DAYTIME - timedelta(days=2)}
        someone_elses_cpf = {"type": "cpf", "belongs_to_destination_owner": False, "created_at": None}
        own_cpf = {"type": "cpf", "belongs_to_destination_owner": True, "created_at": None}

        assert fires(risk_rules.check_pix_key_recently_created, pix_key=new_key)
        assert not fires(risk_rules.check_pix_key_recently_created, pix_key=old_key)
        assert fires(risk_rules.check_pix_key_document_mismatch, pix_key=someone_elses_cpf)
        assert not fires(risk_rules.check_pix_key_document_mismatch, pix_key=own_cpf)
        assert not fires(risk_rules.check_pix_key_document_mismatch, pix_key=new_key)

    def test_invalid_document_names_the_field(self):
        observed = risk_rules.check_invalid_document_number(make_facts(destination={"document_is_valid": False}))
        assert observed == {"invalid_fields": ["destination_account.owner.document_number"]}


class TestDecision:
    def signal(self, action: str, points: int = 0) -> dict:
        return {"rule": "x", "family": "test", "action": action, "points": points, "observed": {}}

    def test_score_bands(self):
        assert risk_rules.decide([], 0)["status"] == "approved"
        assert risk_rules.decide([], 1)["status"] == "approved"
        assert risk_rules.decide([], 2)["status"] == "two_factor_required"
        assert risk_rules.decide([], 4)["status"] == "two_factor_required"
        assert risk_rules.decide([], 5)["status"] == "rejected"

    def test_score_is_capped_at_five(self):
        decision = risk_rules.decide([self.signal("score", 2), self.signal("score", 2)], 4)

        assert decision["risk_score"] == 5
        assert decision["points_from_this_pix"] == 4
        assert decision["status"] == "rejected"

    def test_blocking_rule_wins_even_with_zero_score(self):
        decision = risk_rules.decide([self.signal("reject")], 0)

        assert decision["status"] == "rejected"
        assert decision["reason"] == "blocking_rule"

    def test_missing_data_only_raises_approved_to_two_factor(self):
        """Falta de dado vira fricção: nunca soma ponto, nunca reprova, nunca livra ninguém."""
        assert risk_rules.decide([self.signal("two_factor")], 0)["status"] == "two_factor_required"
        assert risk_rules.decide([self.signal("two_factor")], 0)["risk_score"] == 0
        assert risk_rules.decide([self.signal("two_factor")], 5)["status"] == "rejected"

    def test_challenge_example_asks_two_factor(self):
        """O Pix do desafio, sem histórico: R$ 1.500 às 22:15 para conta de 7 dias.

        noite (+2) + conta nova (+1) = 3, e duas faltas de dado. 2FA, não rejected.
        """
        facts = make_facts(
            amount=150000,
            transaction_date=datetime(2026, 10, 17, 22, 15, tzinfo=BRASILIA),
            payer={"level": None},
            destination={"opening_date": datetime(2026, 10, 10, 9, 0, tzinfo=BRASILIA)},
            history={
                "pix_count": 0,
                "accepted_count": 0,
                "average_ticket": None,
                "known_destination": False,
                "known_device": False,
            },
            new_destinations=1,
        )
        catalog = [
            rule("unknown_level_daily_limit", "two_factor", 0),
            rule("payer_without_history", "two_factor", 0),
            rule("night_limit", "score", 2),
            rule("new_device", "score", 1),
            rule("new_destination_account", "score", 1),
            rule("amount_above_profile", "score", 2),
        ]

        signals = risk_rules.evaluate(catalog, facts)
        decision = risk_rules.decide(signals, previous_points=0)

        assert [signal["rule"] for signal in signals] == [
            "unknown_level_daily_limit",
            "payer_without_history",
            "night_limit",
            "new_destination_account",
        ]
        assert decision["risk_score"] == 3
        assert decision["status"] == "two_factor_required"
