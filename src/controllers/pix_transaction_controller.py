import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError

from connectors import ReceitaConnector, ReceitaUnavailable
from constants import RECEITA_CACHE_HOURS, RECEITA_ENABLED
from controllers import risk_rules
from controllers.base_controller import BaseController
from dtos import AnalysisDTO
from errors import (
    PixTransactionConflict,
    PixTransactionNotFound,
    TwoFactorAlreadyAnswered,
    TwoFactorExpired,
    TwoFactorNotRequired,
)
from models import Analysis, AnalysisStatus, PixTransaction, TwoFactorResult
from repositories import (
    AnalysisRepository,
    DocumentSituationRepository,
    PixTransactionRepository,
    RestrictedPartyRepository,
    RuleRepository,
    RuleResultRepository,
    TwoFactorResultRepository,
)
from utils import clock
from utils.document_number import is_valid_document, only_digits

ARRIVAL_TOLERANCE_MINUTES = 15

PIX_ID_FORMAT = re.compile(r"[A-Za-z0-9._:-]{1,100}")

TWO_FACTOR_EXPIRATION_MINUTES = 3


class PixTransactionController(BaseController):
    def __init__(self) -> None:
        super().__init__(__name__)
        self.pix_transaction_repository = PixTransactionRepository(self.context)
        self.analysis_repository = AnalysisRepository(self.context)
        self.rule_repository = RuleRepository(self.context)
        self.rule_result_repository = RuleResultRepository(self.context)
        self.restricted_party_repository = RestrictedPartyRepository(self.context)
        self.document_situation_repository = DocumentSituationRepository(self.context)
        self.two_factor_result_repository = TwoFactorResultRepository(self.context)

    def analyze(self, payload: dict) -> dict:
        pix_id = payload["id"]
        self.logger.debug(f"Analisando o Pix {pix_id}")

        received_at = clock.now()
        payload_hash = self._hash_payload(payload)

        replay = self._find_replay(pix_id, payload_hash)
        if replay is not None:
            return replay

        pix_data = self._normalize(payload, payload_hash)
        receita = self._consult_receita(pix_data["dest_document"])

        self.pix_transaction_repository.lock_payer(pix_data["source_document"])

        replay = self._find_replay(pix_id, payload_hash)
        if replay is not None:
            return replay

        if receita["source"] == "api":
            self.document_situation_repository.save(pix_data["dest_document"], receita["situation"], received_at)

        facts = self._gather_facts(pix_data, receita)
        rules = self._active_rules()
        metrics = self.rule_repository.list_metrics()

        signals = risk_rules.evaluate(rules, facts)
        metric_scores = risk_rules.score_metrics(signals, metrics)

        transaction_date = pix_data["transaction_date"]
        previous_risks = self.analysis_repository.list_payer_risks(
            pix_data["source_document"],
            transaction_date - timedelta(hours=risk_rules.MEMORY_HOURS),
            transaction_date,
        )
        inherited = risk_rules.inherit_risk(previous_risks)

        decision = risk_rules.decide(signals, metric_scores, inherited["points"])

        self.logger.info(f"Pix {pix_id}: {decision['description']}")

        pix_transaction = self.pix_transaction_repository.create(pix_data)
        analysis = self.analysis_repository.create(
            pix_transaction=pix_transaction,
            status_enumerator=decision["status"],
            transaction_risk=decision["transaction_risk"],
            inherited_risk=decision["inherited_risk"],
            risk_score=decision["risk_score"],
            decision_reason=decision["reason"],
            metadata=self._build_metadata(
                decision, signals, metric_scores, inherited, transaction_date, received_at, receita
            ),
        )

        rules_by_enumerator = {rule.enumerator: rule for rule in rules}
        for signal in signals:
            self.rule_result_repository.create(
                analysis=analysis,
                rule=rules_by_enumerator[signal["rule"]],
                points=signal["points"],
                observed=signal["observed"],
            )

        if decision["status"] == AnalysisStatus.TWO_FACTOR_REQUIRED:
            self.two_factor_result_repository.create_pending(
                analysis,
                created_at=received_at,
                expires_at=received_at + timedelta(minutes=TWO_FACTOR_EXPIRATION_MINUTES),
            )

        return self._save(analysis, pix_id, payload_hash)

    def get(self, pix_id: str) -> dict:
        pix_transaction = self._get_pix_transaction(pix_id)
        return AnalysisDTO.obj_to_dict(pix_transaction.analysis)

    def answer_two_factor(self, pix_id: str, result: str) -> dict:
        self.logger.debug(f"Registrando o 2FA do Pix {pix_id}: {result}")

        pix_transaction = self._get_pix_transaction(pix_id)

        self.pix_transaction_repository.lock_payer(pix_transaction.source_document)

        analysis = pix_transaction.analysis
        status = analysis.status.enumerator

        if status != AnalysisStatus.TWO_FACTOR_REQUIRED:
            raise TwoFactorNotRequired(pix_id, status)

        two_factor_result = analysis.two_factor_result
        self.session.refresh(two_factor_result)
        expires_at = clock.to_local_isoformat(two_factor_result.expires_at)

        if two_factor_result.result == TwoFactorResult.EXPIRED:
            raise TwoFactorExpired(pix_id, expires_at)

        if two_factor_result.result != TwoFactorResult.PENDING:
            raise TwoFactorAlreadyAnswered(pix_id)

        now = clock.now()

        if now > two_factor_result.expires_at:
            self.two_factor_result_repository.resolve(two_factor_result, TwoFactorResult.EXPIRED, now)
            self.session.commit()
            raise TwoFactorExpired(pix_id, expires_at)

        self.two_factor_result_repository.resolve(two_factor_result, result, now)
        self.session.flush()

        analysis_dto = AnalysisDTO.obj_to_dict(analysis)
        self.session.commit()

        return analysis_dto

    def _active_rules(self) -> list:
        all_rules = self.rule_repository.list_all()
        enumerators_in_database = {rule.enumerator for rule in all_rules}

        for enumerator in enumerators_in_database - set(risk_rules.RULE_CHECKS):
            self.logger.warning(f"A regra '{enumerator}' está na tabela rule, mas não existe em risk_rules.py")

        for enumerator in set(risk_rules.RULE_CHECKS) - enumerators_in_database:
            self.logger.warning(f"A regra '{enumerator}' existe em risk_rules.py, mas não está na tabela rule")

        return [rule for rule in all_rules if rule.active]

    def _get_pix_transaction(self, pix_id: str) -> PixTransaction:
        if not PIX_ID_FORMAT.fullmatch(pix_id):
            raise PixTransactionNotFound(pix_id[:100])

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
        if existing.payload_hash != payload_hash:
            raise PixTransactionConflict(existing.pix_id)

        self.logger.info(f"Pix {existing.pix_id} repetido: devolvendo a análise original")
        return {"analysis": AnalysisDTO.obj_to_dict(existing.analysis), "is_replay": True}

    def _save(self, analysis: Analysis, pix_id: str, payload_hash: str) -> dict:
        try:
            self.session.flush()
        except IntegrityError:
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
            "transaction_date": clock.parse_datetime(
                "transaction_date", payload["transaction_date"], clock.PIX_LAUNCH, clock.LATEST_DATE
            ),
            "source_document": only_digits(source_account["owner"]["document_number"]),
            "source_branch": source_account["branch"],
            "source_account": source_account["account_number"],
            "source_opening_date": clock.parse_datetime(
                "source_account.opening_date", source_account["opening_date"], clock.EARLIEST_ACCOUNT, clock.LATEST_DATE
            ),
            "dest_participant": destination_account["participant"],
            "dest_branch": destination_account["branch"],
            "dest_account": destination_account["account_number"],
            "dest_opening_date": clock.parse_datetime(
                "destination_account.opening_date",
                destination_account["opening_date"],
                clock.EARLIEST_ACCOUNT,
                clock.LATEST_DATE,
            ),
            "dest_document": only_digits(destination_account["owner"]["document_number"]),
            "device_id": source["device_id"],
            "channel": source["channel"],
            "ip": source["ip"],
            "latitude": None,
            "longitude": None,
        }

        geolocation = source.get("geolocation")
        if geolocation is not None:
            pix_data["latitude"] = geolocation["latitude"]
            pix_data["longitude"] = geolocation["longitude"]

        return pix_data

    def _consult_receita(self, document: str) -> dict:
        if not RECEITA_ENABLED:
            return {"source": "disabled", "situation": None}

        if not is_valid_document(document):
            return {"source": "skipped", "situation": None}

        cache_start = clock.now() - timedelta(hours=RECEITA_CACHE_HOURS)
        cached = self.document_situation_repository.get_consulted_since(document, cache_start)

        if cached is not None:
            return {"source": "cache", "situation": cached.situation}

        try:
            situation = ReceitaConnector().get_document_situation(document)
        except ReceitaUnavailable as error:
            self.logger.warning(f"Receita indisponível para o destino deste Pix: {error.reason}")
            return {"source": "unavailable", "situation": None, "error": error.reason}

        return {"source": "api", "situation": situation}

    def _gather_facts(self, pix_data: dict, receita: dict) -> dict:
        transaction_date = pix_data["transaction_date"]
        local_transaction_date = clock.to_sao_paulo(transaction_date)
        document = pix_data["source_document"]

        destination = {
            "participant": pix_data["dest_participant"],
            "branch": pix_data["dest_branch"],
            "account": pix_data["dest_account"],
        }

        history_start = transaction_date - timedelta(days=risk_rules.HISTORY_DAYS)

        history = self.pix_transaction_repository.payer_history(
            document=document,
            transaction_date=transaction_date,
            history_start=history_start,
            short_window_start=transaction_date - timedelta(hours=risk_rules.DEVICE_CHANGE_WINDOW_HOURS),
            burst_window_start=transaction_date - timedelta(minutes=risk_rules.BURST_WINDOW_MINUTES),
            destination=destination,
            device_id=pix_data["device_id"],
            hour_band=risk_rules.hour_band(local_transaction_date.hour),
        )

        location = None
        if pix_data["latitude"] is not None:
            location = (pix_data["latitude"], pix_data["longitude"])

        return {
            "amount": pix_data["amount"],
            "transaction_date": local_transaction_date,
            "device_id": pix_data["device_id"],
            "location": location,
            "known_places": self.pix_transaction_repository.known_places(document, history_start, transaction_date),
            "payer": {
                "document": document,
                "document_is_valid": is_valid_document(document),
                "restricted_reason": self._restricted_reason(document),
            },
            "destination": {
                "document": pix_data["dest_document"],
                "document_is_valid": is_valid_document(pix_data["dest_document"]),
                "opening_date": pix_data["dest_opening_date"],
                "restricted_reason": self._restricted_reason(pix_data["dest_document"]),
                "receita_situation": receita["situation"],
            },
            "history": history,
            "destination_payers": self.pix_transaction_repository.count_destination_payers(
                destination,
                transaction_date - timedelta(hours=risk_rules.DESTINATION_PAYERS_WINDOW_HOURS),
                transaction_date,
                document,
            ),
            "last_device_id": self.pix_transaction_repository.last_device_id(document, transaction_date),
            "other_payers_on_device": self.pix_transaction_repository.count_other_payers_on_device(
                pix_data["device_id"],
                transaction_date - timedelta(days=risk_rules.DEVICE_SHARED_WINDOW_DAYS),
                transaction_date,
                document,
            ),
            "other_payers_on_ip": self.pix_transaction_repository.count_other_payers_on_ip(
                pix_data["ip"],
                transaction_date - timedelta(hours=risk_rules.IP_SHARED_WINDOW_HOURS),
                transaction_date,
                document,
            ),
        }

    def _restricted_reason(self, document: str) -> Optional[str]:
        restricted_party = self.restricted_party_repository.get_by_document(document)

        if restricted_party is None:
            return None

        return restricted_party.reason

    def _build_metadata(
        self,
        decision: dict,
        signals: list,
        metric_scores: list,
        inherited: dict,
        transaction_date: datetime,
        received_at: datetime,
        receita: dict,
    ) -> dict:
        offset_minutes = int((transaction_date - received_at).total_seconds() // 60)

        return {
            "risk_score": decision["risk_score"],
            "decision": {
                "reason": decision["reason"],
                "rules": decision["decisive_rules"],
                "description": decision["description"],
            },
            "risk": {
                "transaction_risk": decision["transaction_risk"],
                "inherited_risk": decision["inherited_risk"],
                "inherited_from_pix_id": inherited["from_pix_id"],
                "inherited_fraction": inherited["carried_fraction"],
                "memory_hours": risk_rules.MEMORY_HOURS,
                "two_factor_from": risk_rules.TWO_FACTOR_FROM,
                "reject_from": risk_rules.REJECT_FROM,
                "metrics": metric_scores,
            },
            "arrival": {
                "received_at": clock.to_local_isoformat(received_at),
                "transaction_date_offset_minutes": offset_minutes,
                "far_from_arrival": abs(offset_minutes) > ARRIVAL_TOLERANCE_MINUTES,
                "tolerance_minutes": ARRIVAL_TOLERANCE_MINUTES,
            },
            "receita": receita,
            "signals": signals,
        }

    def _hash_payload(self, payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
