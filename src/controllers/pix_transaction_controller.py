import hashlib
import json
from datetime import timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError

from controllers import risk_rules
from controllers.base_controller import BaseController
from dtos import AnalysisDTO
from errors import PixTransactionConflict, PixTransactionNotFound, TwoFactorAlreadyAnswered, TwoFactorNotRequired
from models import Analysis, AnalysisStatus, PixTransaction
from repositories import (
    AnalysisRepository,
    PixTransactionRepository,
    RestrictedPartyRepository,
    RuleRepository,
    RuleResultRepository,
    TwoFactorResultRepository,
)
from utils import clock
from utils.document_number import is_person, is_valid_document, only_digits


class PixTransactionController(BaseController):
    """Recebe o Pix, junta os fatos, pede a decisão ao motor e grava tudo.

    O motor em si (as regras e a conta do score) mora em
    src/controllers/risk_rules.py, em funções puras. Este controller é o
    que fica em volta: o reenvio, a trava, as consultas e a gravação.
    """

    def __init__(self) -> None:
        super().__init__(__name__)
        self.pix_transaction_repository = PixTransactionRepository(self.context)
        self.analysis_repository = AnalysisRepository(self.context)
        self.rule_repository = RuleRepository(self.context)
        self.rule_result_repository = RuleResultRepository(self.context)
        self.restricted_party_repository = RestrictedPartyRepository(self.context)
        self.two_factor_result_repository = TwoFactorResultRepository(self.context)

    def analyze(self, payload: dict) -> dict:
        """A ordem dos passos é a regra, e cada um tem um porquê:

          1. o mesmo `id` já chegou?   antes de tudo: reenvio não roda regra
          2. normaliza                 documentos só com dígitos, chave em hash
          3. trava o pagador           Pix simultâneos dele passam um por vez
          4. o `id` chegou de novo?    alguém igual pode ter gravado enquanto
                                       este esperava a trava
          5. junta os fatos            as consultas de histórico
          6. avalia e decide           risk_rules, sem banco
          7. grava e salva             a trava solta no commit
        """
        pix_id = payload["id"]
        self.logger.debug(f"Analisando o Pix {pix_id}")

        payload_hash = self._hash_payload(payload)

        replay = self._find_replay(pix_id, payload_hash)
        if replay is not None:
            return replay

        pix_data = self._normalize(payload, payload_hash)

        self.pix_transaction_repository.lock_payer(pix_data["source_document"])

        replay = self._find_replay(pix_id, payload_hash)
        if replay is not None:
            return replay

        facts = self._gather_facts(pix_data)
        rules = self.rule_repository.list_active()

        signals = risk_rules.evaluate(rules, facts)

        transaction_date = pix_data["transaction_date"]
        score_window_start = transaction_date - timedelta(days=risk_rules.SCORE_WINDOW_DAYS)
        previous_points = self.rule_result_repository.sum_payer_points(
            pix_data["source_document"], score_window_start, transaction_date
        )

        decision = risk_rules.decide(signals, previous_points)

        self.logger.info(f"Pix {pix_id}: {decision['status']} (score {decision['risk_score']}, {len(signals)} regras)")

        pix_transaction = self.pix_transaction_repository.create(pix_data)
        analysis = self.analysis_repository.create(
            pix_transaction=pix_transaction,
            status_enumerator=decision["status"],
            risk_score=decision["risk_score"],
            metadata=self._build_metadata(decision, signals),
        )

        rules_by_enumerator = {rule.enumerator: rule for rule in rules}
        for signal in signals:
            self.rule_result_repository.create(
                analysis=analysis,
                rule=rules_by_enumerator[signal["rule"]],
                points=signal["points"],
                observed=signal["observed"],
            )

        return self._save(analysis, pix_id, payload_hash)

    def get(self, pix_id: str) -> dict:
        pix_transaction = self._get_pix_transaction(pix_id)
        return AnalysisDTO.obj_to_dict(pix_transaction.analysis)

    def answer_two_factor(self, pix_id: str, result: str) -> dict:
        """Grava o que o cliente fez com o 2FA, sem tocar na decisão.

        O status da análise continua two_factor_required para sempre: ele
        diz o que o MOTOR decidiu. O resultado do 2FA é outro fato, de
        outro momento, e vai para outra tabela.
        """
        self.logger.debug(f"Registrando o 2FA do Pix {pix_id}: {result}")

        pix_transaction = self._get_pix_transaction(pix_id)

        # Mesma trava do analyze: um 2FA confirmado tira pontos do score,
        # e um Pix do mesmo pagador sendo analisado agora precisa ver isso
        # antes ou depois, nunca pela metade.
        self.pix_transaction_repository.lock_payer(pix_transaction.source_document)

        analysis = pix_transaction.analysis
        status = analysis.status.enumerator

        if status != AnalysisStatus.TWO_FACTOR_REQUIRED:
            raise TwoFactorNotRequired(pix_id, status)

        if analysis.two_factor_result is not None:
            raise TwoFactorAlreadyAnswered(pix_id)

        self.two_factor_result_repository.create(analysis, result)

        try:
            self.session.flush()
        except IntegrityError:
            # Duas respostas ao mesmo tempo: o UNIQUE(analysis_id) barrou
            # a segunda.
            self.session.rollback()
            raise TwoFactorAlreadyAnswered(pix_id)

        analysis_dto = AnalysisDTO.obj_to_dict(analysis)
        self.session.commit()

        return analysis_dto

    def _get_pix_transaction(self, pix_id: str) -> PixTransaction:
        pix_transaction = self.pix_transaction_repository.get_by_pix_id(pix_id)

        if pix_transaction is None:
            raise PixTransactionNotFound(pix_id)

        return pix_transaction

    def _find_replay(self, pix_id: str, payload_hash: str) -> Optional[dict]:
        existing = self.pix_transaction_repository.get_by_pix_id(pix_id)

        if existing is None:
            return None

        return self._replay_or_refuse(existing, payload_hash)

    def _replay_or_refuse(self, existing: PixTransaction, payload_hash: str) -> dict:
        """Mesmo id e mesmos dados: devolve a análise original, sem rodar regra nenhuma.

        Rodar de novo daria OUTRA resposta — o primeiro envio já está no
        histórico e somaria pontos ao segundo. O reenvio precisa devolver
        exatamente o que foi decidido.
        """
        if existing.payload_hash != payload_hash:
            raise PixTransactionConflict(existing.pix_id)

        self.logger.info(f"Pix {existing.pix_id} repetido: devolvendo a análise original")
        return {"analysis": AnalysisDTO.obj_to_dict(existing.analysis), "is_replay": True}

    def _save(self, analysis: Analysis, pix_id: str, payload_hash: str) -> dict:
        try:
            self.session.flush()
        except IntegrityError:
            # O mesmo id chegou ao mesmo tempo por pagadores diferentes:
            # as travas não se cruzam, e quem barra o segundo é o
            # UNIQUE(pix_id) do banco.
            self.session.rollback()

            existing = self.pix_transaction_repository.get_by_pix_id(pix_id)
            if existing is None:
                raise

            return self._replay_or_refuse(existing, payload_hash)

        analysis_dto = AnalysisDTO.obj_to_dict(analysis)
        self.session.commit()

        return {"analysis": analysis_dto, "is_replay": False}

    def _normalize(self, payload: dict, payload_hash: str) -> dict:
        source_account = payload["source_account"]
        destination_account = payload["destination_account"]
        source = payload["source"]

        pix_data = {
            "pix_id": payload["id"],
            "payload_hash": payload_hash,
            "amount": payload["amount"],
            "transaction_date": clock.parse_datetime("transaction_date", payload["transaction_date"]),
            "source_document": only_digits(source_account["owner"]["document_number"]),
            "source_level": source_account["owner"].get("level"),
            "source_branch": source_account["branch"],
            "source_account": source_account["account_number"],
            "source_opening_date": clock.parse_datetime("source_account.opening_date", source_account["opening_date"]),
            "dest_participant": destination_account["participant"],
            "dest_branch": destination_account["branch"],
            "dest_account": destination_account["account_number"],
            "dest_opening_date": clock.parse_datetime(
                "destination_account.opening_date", destination_account["opening_date"]
            ),
            "dest_document": only_digits(destination_account["owner"]["document_number"]),
            "pix_key_type": None,
            "pix_key_hash": None,
            "pix_key_created_at": None,
            "device_id": source["device_id"],
            "channel": source["channel"],
            "ip": source["ip"],
            "latitude": None,
            "longitude": None,
        }

        pix_key = destination_account.get("pix_key")
        if pix_key is not None:
            pix_data["pix_key_type"] = pix_key["type"]
            pix_data["pix_key_hash"] = self._hash_pix_key(pix_key["type"], pix_key["value"])

            if pix_key.get("created_at") is not None:
                pix_data["pix_key_created_at"] = clock.parse_datetime(
                    "destination_account.pix_key.created_at", pix_key["created_at"]
                )

        geolocation = source.get("geolocation")
        if geolocation is not None:
            pix_data["latitude"] = geolocation["latitude"]
            pix_data["longitude"] = geolocation["longitude"]

        return pix_data

    def _gather_facts(self, pix_data: dict) -> dict:
        """Os números de que as regras precisam, buscados no banco de uma vez.

        Todas as janelas contam a partir da data do PIX (transaction_date),
        e não do relógio do servidor: a regra pergunta "o que este pagador
        fez nas 24 horas antes deste Pix".
        """
        transaction_date = pix_data["transaction_date"]
        document = pix_data["source_document"]
        short_window_start = transaction_date - timedelta(hours=risk_rules.SHORT_WINDOW_HOURS)

        destination = {
            "participant": pix_data["dest_participant"],
            "branch": pix_data["dest_branch"],
            "account": pix_data["dest_account"],
        }

        history = self.pix_transaction_repository.payer_history(
            document=document,
            transaction_date=transaction_date,
            history_start=transaction_date - timedelta(days=risk_rules.HISTORY_DAYS),
            short_window_start=short_window_start,
            day_start=clock.day_start(transaction_date),
            destination=destination,
            device_id=pix_data["device_id"],
            night_start_hour=risk_rules.NIGHT_START_HOUR,
            night_end_hour=risk_rules.NIGHT_END_HOUR,
            split_min_amount=risk_rules.SPLIT_MIN_AMOUNT,
            split_max_amount=risk_rules.SPLIT_MAX_AMOUNT,
        )

        pix_key_other_accounts = 0
        if pix_data["pix_key_hash"] is not None:
            pix_key_other_accounts = self.pix_transaction_repository.count_other_accounts_for_pix_key(
                pix_data["pix_key_hash"],
                transaction_date - timedelta(days=risk_rules.PIX_KEY_WINDOW_DAYS),
                transaction_date,
                destination,
            )

        restricted_party = self.restricted_party_repository.get_by_document(pix_data["dest_document"])
        restricted_reason = None
        if restricted_party is not None:
            restricted_reason = restricted_party.reason

        pix_key = None
        if pix_data["pix_key_type"] is not None:
            pix_key = {
                "type": pix_data["pix_key_type"],
                "belongs_to_destination_owner": self._pix_key_belongs_to_destination_owner(pix_data),
                "created_at": pix_data["pix_key_created_at"],
            }

        return {
            "amount": pix_data["amount"],
            "transaction_date": clock.to_sao_paulo(transaction_date),
            "device_id": pix_data["device_id"],
            "payer": {
                "document": document,
                "document_is_valid": is_valid_document(document),
                "is_person": is_person(document),
                "level": pix_data["source_level"],
            },
            "destination": {
                "document": pix_data["dest_document"],
                "document_is_valid": is_valid_document(pix_data["dest_document"]),
                "opening_date": pix_data["dest_opening_date"],
                "restricted_reason": restricted_reason,
            },
            "pix_key": pix_key,
            "history": history,
            "new_destinations": self.pix_transaction_repository.count_new_destinations(
                document, short_window_start, transaction_date, destination
            ),
            "destination_payers": self.pix_transaction_repository.count_destination_payers(
                destination, short_window_start, transaction_date, document
            ),
            "pix_key_other_accounts": pix_key_other_accounts,
        }

    def _build_metadata(self, decision: dict, signals: list) -> dict:
        """O "motivo explicável" do desafio: o score, de onde ele veio, e o que cada regra viu."""
        return {
            "risk_score": decision["risk_score"],
            "decision_reason": decision["reason"],
            "score": {
                "previous_points": decision["previous_points"],
                "points_from_this_pix": decision["points_from_this_pix"],
                "window_days": risk_rules.SCORE_WINDOW_DAYS,
                "max": risk_rules.MAX_SCORE,
            },
            "signals": signals,
        }

    def _pix_key_belongs_to_destination_owner(self, pix_data: dict) -> Optional[bool]:
        """Para chave CPF/CNPJ: a chave é o documento do dono da conta?

        O valor da chave não fica guardado em lugar nenhum, nem aqui: para
        comparar, basta passar o documento do dono pelo mesmo hash. Chave
        de e-mail, telefone ou aleatória não diz de quem é — None.
        """
        if pix_data["pix_key_type"] not in risk_rules.PERSON_KEY_TYPES:
            return None

        owner_hash = self._hash_pix_key(pix_data["pix_key_type"], pix_data["dest_document"])
        return pix_data["pix_key_hash"] == owner_hash

    def _hash_payload(self, payload: dict) -> str:
        """A impressão digital do pedido, para reconhecer o reenvio.

        `sort_keys` faz a ordem dos campos não importar: o mesmo JSON com
        os campos em outra ordem continua sendo o mesmo pedido.
        """
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _hash_pix_key(self, key_type: str, key_value: str) -> str:
        """O hash da chave, depois de escrever ela de um jeito só.

        Sem normalizar, "Fulano@Email.com" e "fulano@email.com" seriam
        duas chaves, e a regra de chave que mudou de conta não veria nada.
        """
        if key_type in risk_rules.PERSON_KEY_TYPES:
            normalized = only_digits(key_value)
        else:
            normalized = key_value.strip().lower()

        return hashlib.sha256(f"{key_type}:{normalized}".encode("utf-8")).hexdigest()
