from datetime import timedelta

from tests.utils import DAYTIME, NIGHTTIME, ObjectGenerator, PayloadGenerator, RandomGenerator

RESTRICTED_DOCUMENT = "529.982.247-25"  # está no seed de restricted_party do database.sql


class TestBlockingRules:
    def test_invalid_document_is_rejected_and_recorded(self):
        """Documento que não existe não vira 4xx: o Pix é reprovado e fica gravado e consultável."""
        destination = PayloadGenerator.create_destination(document_number="111.222.333-44")

        analysis = ObjectGenerator.send_pix(destination=destination)

        assert analysis["status"] == "rejected"
        assert analysis["metadata"]["decision_reason"] == "blocking_rule"
        assert "invalid_document_number" in ObjectGenerator.fired_rules(analysis)

    def test_restricted_destination_is_rejected(self):
        destination = PayloadGenerator.create_destination(document_number=RESTRICTED_DOCUMENT)

        analysis = ObjectGenerator.send_pix(destination=destination)

        assert analysis["status"] == "rejected"
        assert "restricted_destination" in ObjectGenerator.fired_rules(analysis)

    def test_silver_over_daily_limit_is_rejected_but_gold_is_not(self):
        silver = ObjectGenerator.send_pix(amount=150000, level="silver")
        gold = ObjectGenerator.send_pix(amount=150000, level="gold")

        assert silver["status"] == "rejected"
        assert "silver_daily_limit_exceeded" in ObjectGenerator.fired_rules(silver)

        # O Ouro não tem teto; o 2FA dele vem de ser o primeiro Pix (falta de dado).
        assert gold["status"] == "two_factor_required"
        assert ObjectGenerator.fired_rules(gold) == ["payer_without_history"]

    def test_cpf_key_of_someone_else_is_rejected(self):
        destination = PayloadGenerator.create_destination()
        someone_else = RandomGenerator.generate_cpf()

        analysis = ObjectGenerator.send_pix(destination=destination, pix_key={"type": "cpf", "value": someone_else})
        own_key = ObjectGenerator.send_pix(
            destination=destination,
            pix_key={"type": "cpf", "value": destination["owner"]["document_number"]},
        )

        assert analysis["status"] == "rejected"
        assert "pix_key_document_mismatch" in ObjectGenerator.fired_rules(analysis)
        assert own_key["status"] == "approved"


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

    def test_same_pix_key_on_another_account(self):
        pix_key = {"type": "evp", "value": f"chave-{RandomGenerator.generate_cpf()}"}

        first = ObjectGenerator.send_pix(pix_key=pix_key)
        moved = ObjectGenerator.send_pix(pix_key=pix_key, transaction_date=DAYTIME + timedelta(hours=1))

        assert "pix_key_moved_account" not in ObjectGenerator.fired_rules(first)
        assert "pix_key_moved_account" in ObjectGenerator.fired_rules(moved)

    def test_new_device_on_payer_with_history(self):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_pix(payer_document=payer, device_id="celular-de-sempre")
        other_device = ObjectGenerator.send_pix(
            payer_document=payer, device_id="celular-novo", transaction_date=DAYTIME + timedelta(hours=1)
        )

        assert "new_device" in ObjectGenerator.fired_rules(other_device)
