import random
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tests.utils.random_generator import RandomGenerator

# O fuso de Brasília escrito como deslocamento fixo. Os testes montam as
# datas aqui e o motor converte para São Paulo do lado de lá.
BRASILIA = timezone(timedelta(hours=-3))

# Uma tarde qualquer, longe do limite noturno. Os testes que precisam de
# noite pedem explicitamente.
DAYTIME = datetime(2026, 10, 14, 14, 0, tzinfo=BRASILIA)
NIGHTTIME = datetime(2026, 10, 14, 22, 15, tzinfo=BRASILIA)

ITAU_ISPB = "60701190"

# Lugares para o Modo Rua. Guarulhos fica a ~17 km do centro de São
# Paulo (dentro do raio de 30 km); Campinas, a ~85 km; Salvador, a
# ~1.450 km.
SAO_PAULO = {"latitude": -23.56, "longitude": -46.65}
GUARULHOS = {"latitude": -23.45, "longitude": -46.53}
CAMPINAS = {"latitude": -22.91, "longitude": -47.06}
SALVADOR = {"latitude": -12.97, "longitude": -38.50}


def to_iso(moment: datetime) -> str:
    return moment.isoformat()


class PayloadGenerator:
    @staticmethod
    def create_destination(opening_date: datetime = None, document_number: str = None) -> dict:
        """Uma conta de destino nova a cada chamada: cada teste tem as suas.

        Aberta há um ano por padrão, para não disparar a regra de conta
        nova sem o teste pedir.
        """
        if opening_date is None:
            opening_date = DAYTIME - timedelta(days=365)

        if document_number is None:
            document_number = RandomGenerator.generate_cpf()

        return {
            "participant": ITAU_ISPB,
            "branch": str(random.randrange(1000, 9999)),
            "account_number": str(random.randrange(10000, 99999999)),
            "opening_date": to_iso(opening_date),
            "owner": {"document_number": document_number},
        }

    @staticmethod
    def create_pix_payload(
        amount: int = 5000,
        transaction_date: datetime = None,
        payer_document: str = None,
        destination: dict = None,
        device_id: str = None,
        ip: str = None,
        geolocation: dict = SAO_PAULO,
        pix_id: str = None,
    ) -> dict:
        """Um Pix válido, com qualquer campo trocado a pedido.

        Sem argumento nenhum o pagador é um CPF aleatório: cada teste
        começa com um pagador sem histórico, e um teste não contamina o
        risco do outro. Quem testa histórico passa o MESMO pagador nos
        vários Pix.

        O aparelho e o IP, por padrão, nascem do CPF do pagador: um
        aparelho por conta, como na premissa do projeto. Um aparelho ou
        um IP fixo para todos os testes faria a métrica de origem pesar
        em todos eles.

        A geolocalização é São Paulo por padrão. `geolocation=None` manda
        o Pix sem ela, como o contrato permite.
        """
        if transaction_date is None:
            transaction_date = DAYTIME

        if payer_document is None:
            payer_document = RandomGenerator.generate_cpf()

        if destination is None:
            destination = PayloadGenerator.create_destination()

        payer_digits = "".join(character for character in payer_document if character.isdigit())

        if device_id is None:
            device_id = f"dvc-{payer_digits}"

        if ip is None:
            number = int(payer_digits)
            ip = f"10.{number // 65536 % 256}.{number // 256 % 256}.{number % 256}"

        if pix_id is None:
            pix_id = f"pix-{uuid4()}"

        owner = {"document_number": payer_document}

        source = {
            "device_id": device_id,
            "channel": "app",
            "ip": ip,
        }
        if geolocation is not None:
            source["geolocation"] = dict(geolocation)

        return {
            "id": pix_id,
            "amount": amount,
            "transaction_date": to_iso(transaction_date),
            "source_account": {
                "branch": "0001",
                "account_number": "88213",
                "opening_date": "2025-03-02T10:00:00-03:00",
                "owner": owner,
            },
            "destination_account": destination,
            "source": source,
        }

    @staticmethod
    def create_challenge_payload(pix_id: str = None) -> dict:
        """O Pix do exemplo do desafio, campo por campo (só o id muda, para não colidir)."""
        if pix_id is None:
            pix_id = f"pix-{uuid4()}"

        return {
            "id": pix_id,
            "amount": 150000,
            "transaction_date": "2026-10-17T22:15:00-03:00",
            "source_account": {
                "branch": "0001",
                "account_number": "88213",
                "opening_date": "2025-03-02T10:00:00-03:00",
                "owner": {"document_number": "123.456.789-09"},
            },
            "destination_account": {
                "participant": "60701190",
                "branch": "3675",
                "account_number": "10442",
                "opening_date": "2026-10-10T09:00:00-03:00",
                "owner": {"document_number": "987.654.321-00"},
            },
            "source": {
                "device_id": "dvc-9f2c71a8",
                "channel": "app",
                "ip": "198.51.100.23",
                "geolocation": {"latitude": -23.56, "longitude": -46.65},
            },
        }
