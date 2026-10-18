from typing import List

from database import Context
from models import RiskMetric, Rule


class RuleRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def list_all(self) -> List[Rule]:
        """O catálogo de sinais inteiro, ligados e desligados, na ordem em que foram cadastrados.

        Quem filtra os ligados é o controller: ele precisa do catálogo
        inteiro para avisar quando o código e o banco não batem.
        """
        return self.session.query(Rule).order_by(Rule.id).all()

    def list_metrics(self) -> List[RiskMetric]:
        """As métricas, da que mais pesa para a que menos pesa."""
        return self.session.query(RiskMetric).order_by(RiskMetric.max_points.desc(), RiskMetric.id).all()
