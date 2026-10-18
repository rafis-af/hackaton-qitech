from datetime import datetime

from database import Context
from models import Analysis, TwoFactorResult


class TwoFactorResultRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create_pending(self, analysis: Analysis, created_at: datetime, expires_at: datetime) -> TwoFactorResult:
        two_factor_result = TwoFactorResult()

        two_factor_result.analysis = analysis
        two_factor_result.result = TwoFactorResult.PENDING
        two_factor_result.created_at = created_at
        two_factor_result.expires_at = expires_at

        self.session.add(two_factor_result)
        return two_factor_result

    def reload(self, two_factor_result: TwoFactorResult) -> None:
        self.session.refresh(two_factor_result)

    def resolve(self, two_factor_result: TwoFactorResult, result: str, resolved_at: datetime) -> None:
        two_factor_result.result = result
        two_factor_result.resolved_at = resolved_at
