from typing import List

from database import Context
from models import Rule


class RuleRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def list_active(self) -> List[Rule]:
        """As regras ligadas. Desligar uma regra é um UPDATE em `rule.active`, sem deploy."""
        return self.session.query(Rule).filter(Rule.active.is_(True)).order_by(Rule.id).all()
