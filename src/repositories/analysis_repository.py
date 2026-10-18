from datetime import datetime
from typing import List
from uuid import uuid4

from database import Context
from models import Analysis, AnalysisStatus, PixTransaction, TwoFactorResult


class AnalysisRepository:
    """A decisão do motor. Repare que não existe `update` aqui: a análise é imutável."""

    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create(
        self,
        pix_transaction: PixTransaction,
        status_enumerator: str,
        transaction_risk: int,
        inherited_risk: int,
        risk_score: int,
        decision_reason: str,
        metadata: dict,
    ) -> Analysis:
        analysis = Analysis()

        analysis.analysis_key = str(uuid4())
        analysis.pix_transaction = pix_transaction
        analysis.status = self.get_status(status_enumerator)
        analysis.transaction_risk = transaction_risk
        analysis.inherited_risk = inherited_risk
        analysis.risk_score = risk_score
        analysis.decision_reason = decision_reason
        analysis.analysis_metadata = metadata

        self.session.add(analysis)
        return analysis

    def get_status(self, enumerator: str) -> AnalysisStatus:
        return self.session.query(AnalysisStatus).filter(AnalysisStatus.enumerator == enumerator).one()

    def list_payer_risks(self, document: str, window_start: datetime, transaction_date: datetime) -> List[dict]:
        """O risco de cada Pix do pagador ainda dentro da validade, com o que aconteceu no 2FA dele.

        É a matéria-prima da memória do risco. Quem decide quanto cada um
        deixa de herança é o risk_rules: aqui só se busca.
        """
        rows = (
            self.session.query(PixTransaction.pix_id, Analysis.transaction_risk, TwoFactorResult.result)
            .select_from(Analysis)
            .join(PixTransaction, PixTransaction.id == Analysis.pix_transaction_id)
            .outerjoin(TwoFactorResult, TwoFactorResult.analysis_id == Analysis.id)
            .filter(
                PixTransaction.source_document == document,
                PixTransaction.transaction_date > window_start,
                PixTransaction.transaction_date <= transaction_date,
            )
            .all()
        )

        return [
            {"pix_id": pix_id, "transaction_risk": transaction_risk, "two_factor_result": result}
            for pix_id, transaction_risk, result in rows
        ]
