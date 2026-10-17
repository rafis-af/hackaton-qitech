from uuid import uuid4

from database import Context
from models import Analysis, AnalysisStatus, PixTransaction


class AnalysisRepository:
    """A decisão do motor. Repare que não existe `update` aqui: a análise é imutável."""

    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create(self, pix_transaction: PixTransaction, status_enumerator: str, risk_score: int, metadata: dict) -> Analysis:
        analysis = Analysis()

        analysis.analysis_key = str(uuid4())
        analysis.pix_transaction = pix_transaction
        analysis.status = self.get_status(status_enumerator)
        analysis.risk_score = risk_score
        analysis.analysis_metadata = metadata

        self.session.add(analysis)
        return analysis

    def get_status(self, enumerator: str) -> AnalysisStatus:
        return self.session.query(AnalysisStatus).filter(AnalysisStatus.enumerator == enumerator).one()
