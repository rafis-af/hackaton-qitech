from database import Context
from models import Analysis, Rule, RuleResult


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
