from database import Context
from models import Analysis, TwoFactorResult


class TwoFactorResultRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create(self, analysis: Analysis, result: str) -> TwoFactorResult:
        two_factor_result = TwoFactorResult()

        two_factor_result.analysis = analysis
        two_factor_result.result = result

        self.session.add(two_factor_result)
        return two_factor_result
