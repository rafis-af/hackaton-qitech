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
        "street_mode": None,
        "history": {
            "pix_count": 10,
            "accepted_count": 10,
            "average_ticket": 10000,
            "sum_today": 0,
            "sum_short_window": 0,
            "split_count": 0,
            "known_destination": True,
            "other_device_in_short_window": False,
            "failed_two_factor_count": 0,
        },
        "new_destinations": 0,
        "destination_payers": 1,
        "last_device_id": "dvc-1",
        "other_payers_on_device": 0,
    }

    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(facts.get(key), dict):
            facts[key] = {**facts[key], **value}
        else:
            facts[key] = value

    return facts


def rule(enumerator: str, action: str = "score", points: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        enumerator=enumerator, family="test", action=action, points=points, description=f"descrição de {enumerator}"
    )


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

    def test_street_mode_without_limit_asks_every_pix(self):
        check = risk_rules.check_street_mode

        assert fires(check, street_mode={"active": True, "limit": None}, amount=1)
        assert not fires(check, street_mode={"active": False, "limit": None}, amount=900000)
        # Sem o campo no payload, o Modo Rua não existe para este Pix
        assert not fires(check, street_mode=None, amount=900000)

    def test_street_mode_with_limit_asks_only_above_it(self):
        check = risk_rules.check_street_mode
        street_mode = {"active": True, "limit": 20000}

        assert not fires(check, street_mode=street_mode, amount=20000)
        assert fires(check, street_mode=street_mode, amount=20001)

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

    def test_device_changed_now_and_in_the_next_hours(self):
        check = risk_rules.check_device_changed

        # o último Pix foi de outro aparelho: a troca é agora
        assert fires(check, last_device_id="dvc-antigo")
        # o último já foi deste aparelho, mas houve outro nas últimas 24h
        assert fires(check, history={"other_device_in_short_window": True})
        # mesmo aparelho de sempre
        assert not fires(check)
        # primeiro Pix da vida: não há aparelho antigo para comparar
        assert not fires(check, last_device_id=None)

    def test_device_shared_with_another_payer(self):
        assert fires(risk_rules.check_device_shared, other_payers_on_device=1)
        assert not fires(risk_rules.check_device_shared, other_payers_on_device=0)

    def test_new_destination_account_is_under_thirty_days(self):
        check = risk_rules.check_new_destination_account

        assert fires(check, destination={"opening_date": DAYTIME - timedelta(days=29, hours=23)})
        assert not fires(check, destination={"opening_date": DAYTIME - timedelta(days=30)})

    def test_counterparty_thresholds(self):
        assert fires(risk_rules.check_many_new_destinations, new_destinations=5)
        assert not fires(risk_rules.check_many_new_destinations, new_destinations=4)
        assert fires(risk_rules.check_destination_many_payers, destination_payers=5)
        assert not fires(risk_rules.check_destination_many_payers, destination_payers=4)
        assert fires(risk_rules.check_restricted_destination, destination={"restricted_reason": "confirmed_fraud"})
        assert fires(risk_rules.check_two_factor_failed_recently, history={"failed_two_factor_count": 1})

    def test_invalid_document_names_the_field(self):
        observed = risk_rules.check_invalid_document_number(make_facts(destination={"document_is_valid": False}))
        assert observed == {"invalid_fields": ["destination_account.owner.document_number"]}


class TestDecision:
    def signal(self, action: str, points: int = 0, name: str = "x") -> dict:
        return {
            "rule": name, "family": "test", "action": action, "points": points,
            "description": f"descrição de {name}", "observed": {},
        }

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

    def test_blocking_rule_names_who_blocked(self):
        decision = risk_rules.decide(
            [self.signal("reject", name="restricted_destination"), self.signal("score", 1, name="night_limit")], 0
        )

        assert decision["status"] == "rejected"
        assert decision["reason"] == "blocking_rule"
        assert decision["decisive_rules"] == ["restricted_destination"]
        assert decision["description"] == "Reprovado por regra de bloqueio: descrição de restricted_destination"

    def test_rejected_by_score_names_the_rules_of_this_pix(self):
        decision = risk_rules.decide([self.signal("score", 2, name="split_below_night_limit")], 4)

        assert decision["reason"] == "risk_score"
        assert decision["decisive_rules"] == ["split_below_night_limit"]
        assert decision["description"].startswith("Reprovado: score de risco 5 de 5")

    def test_two_factor_rules_only_raise_approved(self):
        """Falta de dado e Modo Rua viram fricção: nunca somam ponto, nunca reprovam, nunca livram ninguém."""
        street = [self.signal("two_factor", name="street_mode")]

        assert risk_rules.decide(street, 0)["status"] == "two_factor_required"
        assert risk_rules.decide(street, 0)["reason"] == "two_factor_rule"
        assert risk_rules.decide(street, 0)["decisive_rules"] == ["street_mode"]
        assert risk_rules.decide(street, 0)["risk_score"] == 0
        assert risk_rules.decide(street, 5)["status"] == "rejected"

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
            },
            last_device_id=None,
            new_destinations=1,
        )
        catalog = [
            rule("unknown_level_daily_limit", "two_factor", 0),
            rule("payer_without_history", "two_factor", 0),
            rule("night_limit", "score", 2),
            rule("device_changed", "score", 1),
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
