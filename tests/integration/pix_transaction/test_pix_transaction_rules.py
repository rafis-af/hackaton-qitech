from datetime import timedelta

from tests.utils import DAYTIME, NIGHTTIME, ObjectGenerator, PayloadGenerator, RandomGenerator, RequestGenerator

RESTRICTED_DOCUMENT = "529.982.247-25"  # está no seed de restricted_party do database.sql


class TestBlockingRules:
    def test_invalid_document_is_rejected_and_recorded(self):
        """Documento que não existe não vira 4xx: o Pix é reprovado e fica gravado e consultável."""
        destination = PayloadGenerator.create_destination(document_number="111.222.333-44")

        analysis = ObjectGenerator.send_pix(destination=destination)

        assert analysis["status"] == "rejected"
        assert ObjectGenerator.decision(analysis)["reason"] == "blocking_rule"
        assert ObjectGenerator.decision(analysis)["rules"] == ["invalid_document_number"]

    def test_rejected_pix_is_recorded_with_the_reason(self):
        """O Pix barrado fica gravado, e o GET diz por que ele foi barrado."""
        destination = PayloadGenerator.create_destination(document_number=RESTRICTED_DOCUMENT)

        analysis = ObjectGenerator.send_pix(destination=destination)
        status, saved = RequestGenerator.GET_pix_transaction(analysis["pix_id"])

        assert status == 200
        assert saved["status"] == "rejected"
        assert ObjectGenerator.decision(saved) == {
            "reason": "blocking_rule",
            "rules": ["restricted_destination"],
            "description": "Reprovado por regra de bloqueio: Quem recebe está na lista restritiva",
        }

    def test_silver_over_daily_limit_is_rejected_but_gold_is_not(self):
        silver = ObjectGenerator.send_pix(amount=150000, level="silver")
        gold = ObjectGenerator.send_pix(amount=150000, level="gold")

        assert silver["status"] == "rejected"
        assert "silver_daily_limit_exceeded" in ObjectGenerator.fired_rules(silver)

        # O Ouro não tem teto; o 2FA dele vem de ser o primeiro Pix (falta de dado).
        assert gold["status"] == "two_factor_required"
        assert ObjectGenerator.fired_rules(gold) == ["payer_without_history"]


class TestStreetMode:
    """O Modo Rua: o app avisa que o celular saiu das redes Wi-Fi que o cliente cadastrou."""

    def test_without_limit_every_pix_asks_two_factor(self):
        analysis = ObjectGenerator.send_pix(amount=1000, street_mode={"active": True})

        assert analysis["status"] == "two_factor_required"
        assert ObjectGenerator.decision(analysis)["reason"] == "two_factor_rule"
        assert ObjectGenerator.decision(analysis)["rules"] == ["street_mode"]
        # Fricção, não comportamento suspeito: não soma ponto
        assert analysis["metadata"]["risk_score"] == 0

    def test_with_limit_only_above_it_asks_two_factor(self):
        street_mode = {"active": True, "limit": 10000}

        below = ObjectGenerator.send_pix(amount=10000, street_mode=street_mode)
        above = ObjectGenerator.send_pix(amount=10001, street_mode=street_mode)

        assert below["status"] == "approved"
        assert above["status"] == "two_factor_required"
        assert "street_mode" in ObjectGenerator.fired_rules(above)

    def test_inactive_or_absent_changes_nothing(self):
        at_home = ObjectGenerator.send_pix(amount=10000, street_mode={"active": False})
        not_informed = ObjectGenerator.send_pix(amount=10000)

        assert at_home["status"] == "approved"
        assert not_informed["status"] == "approved"

    def test_street_mode_never_saves_a_rejected_pix(self):
        destination = PayloadGenerator.create_destination(document_number=RESTRICTED_DOCUMENT)

        analysis = ObjectGenerator.send_pix(destination=destination, street_mode={"active": True})

        assert analysis["status"] == "rejected"


class TestOneDevicePerAccount:
    """Premissa do projeto: um aparelho por conta e uma conta por aparelho."""

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

    def test_same_device_on_another_account(self):
        device_id = f"celular-{RandomGenerator.generate_cpf()}"

        first_owner = ObjectGenerator.send_pix(device_id=device_id)
        second_account = ObjectGenerator.send_pix(device_id=device_id, transaction_date=DAYTIME + timedelta(hours=1))

        assert "device_shared" not in ObjectGenerator.fired_rules(first_owner)
        assert "device_shared" in ObjectGenerator.fired_rules(second_account)
        assert second_account["status"] == "two_factor_required"


class TestScore:
    def test_splitting_below_night_limit_climbs_to_rejected(self):
        """Fracionamento: R$ 900 à noite, um atrás do outro, cada um para uma conta nova.

        Nenhum Pix sozinho passa do limite noturno. É o padrão que pontua,
        e o score sobe Pix a Pix até reprovar.
        """
        payer = RandomGenerator.generate_cpf()

        results = []
        for minute in range(6):
            analysis = ObjectGenerator.send_pix(
                payer_document=payer,
                amount=90000,
                transaction_date=NIGHTTIME + timedelta(minutes=minute),
            )
            results.append((analysis["status"], analysis["metadata"]["risk_score"]))

        # O reprovado diz que foi o score, e quais regras deste Pix o levaram a 5.
        assert ObjectGenerator.decision(analysis)["reason"] == "risk_score"
        assert ObjectGenerator.decision(analysis)["rules"] == ["many_new_destinations", "split_below_night_limit"]

        assert results == [
            ("two_factor_required", 0),  # primeiro Pix: falta de dado, não ponto
            ("two_factor_required", 0),  # o dia passou de R$ 1.000 sem nível informado
            ("two_factor_required", 2),  # 3º Pix logo abaixo do limite: +2
            ("two_factor_required", 4),  # 4º: +2
            ("rejected", 5),  # 5º: fracionamento +2 e 5 destinos novos +2
            ("rejected", 5),
        ]

    def test_high_amount_to_known_destination_is_approved(self):
        """A tese: R$ 5.000 para o destino de sempre é o aluguel. Para um destino novo, é dúvida."""
        payer = RandomGenerator.generate_cpf()
        rent = PayloadGenerator.create_destination()

        for days_ago in [3, 2, 1]:
            ObjectGenerator.send_pix(
                payer_document=payer, level="gold", destination=rent, amount=50000,
                transaction_date=DAYTIME - timedelta(days=days_ago),
            )

        to_rent = ObjectGenerator.send_pix(payer_document=payer, level="gold", destination=rent, amount=500000)
        to_stranger = ObjectGenerator.send_pix(
            payer_document=payer, level="gold", amount=900000, transaction_date=DAYTIME + timedelta(minutes=5)
        )

        assert to_rent["status"] == "approved"
        assert "amount_above_profile" not in ObjectGenerator.fired_rules(to_rent)

        assert to_stranger["status"] == "two_factor_required"
        assert "amount_above_profile" in ObjectGenerator.fired_rules(to_stranger)

    def test_points_expire_after_seven_days(self):
        payer = RandomGenerator.generate_cpf()

        # Fracionamento até o 5º Pix, que reprova.
        for minute in range(5):
            ObjectGenerator.send_pix(payer_document=payer, amount=90000, transaction_date=NIGHTTIME + timedelta(minutes=minute))

        a_week_later = ObjectGenerator.send_pix(
            payer_document=payer, amount=5000, transaction_date=DAYTIME + timedelta(days=8)
        )

        assert a_week_later["metadata"]["score"]["previous_points"] == 0
        assert a_week_later["status"] == "approved"

    def test_destination_receiving_from_many_payers(self):
        """Conta laranja: cinco pagadores diferentes na mesma conta em poucas horas."""
        destination = PayloadGenerator.create_destination()

        analyses = []
        for minute in range(5):
            analyses.append(
                ObjectGenerator.send_pix(destination=destination, transaction_date=DAYTIME + timedelta(minutes=minute))
            )

        assert "destination_many_payers" not in ObjectGenerator.fired_rules(analyses[3])
        assert "destination_many_payers" in ObjectGenerator.fired_rules(analyses[4])
        assert analyses[4]["status"] == "two_factor_required"
