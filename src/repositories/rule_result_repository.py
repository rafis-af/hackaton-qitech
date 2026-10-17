from datetime import datetime

from sqlalchemy import func, or_

from database import Context
from models import Analysis, PixTransaction, Rule, RuleResult, TwoFactorResult


class RuleResultRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create(self, analysis: Analysis, rule: Rule, points: int, observed: dict) -> RuleResult:
        rule_result = RuleResult()

        rule_result.analysis = analysis
        rule_result.rule = rule
        rule_result.points = points
        rule_result.observed = observed

        self.session.add(rule_result)
        return rule_result

    def sum_payer_points(self, document: str, window_start: datetime, transaction_date: datetime) -> int:
        """Os pontos do pagador na janela: é daqui que sai o score.

        O score não é guardado em lugar nenhum, e é por isso que os pontos
        expiram sozinhos: o que saiu da janela simplesmente deixa de ser
        somado. Não há contador para zerar nem processo para limpar.

        Pontos de um Pix com 2FA CONFIRMADO não entram. A dúvida que
        gerou aqueles pontos foi tirada pelo próprio cliente.
        """
        total = (
            self.session.query(func.coalesce(func.sum(RuleResult.points), 0))
            .join(Analysis, Analysis.id == RuleResult.analysis_id)
            .join(PixTransaction, PixTransaction.id == Analysis.pix_transaction_id)
            .outerjoin(TwoFactorResult, TwoFactorResult.analysis_id == Analysis.id)
            .filter(
                PixTransaction.source_document == document,
                PixTransaction.transaction_date > window_start,
                PixTransaction.transaction_date <= transaction_date,
                or_(TwoFactorResult.result.is_(None), TwoFactorResult.result != TwoFactorResult.CONFIRMED),
            )
            .scalar()
        )
        return int(total)
