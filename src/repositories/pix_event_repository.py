from datetime import datetime
from typing import Optional

from database import Context
from models import PixEvent, PixEventType, PixTransaction


class PixEventRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create(
        self,
        pix_transaction: PixTransaction,
        type_enumerator: str,
        request_id: str,
        created_at: datetime,
        error_code: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> PixEvent:
        event = PixEvent()

        event.event_type = self.get_type(type_enumerator)
        event.request_id = request_id
        event.error_code = error_code
        event.details = details or {}
        event.created_at = created_at

        pix_transaction.events.append(event)
        self.session.add(event)
        return event

    def get_type(self, enumerator: str) -> PixEventType:
        return self.session.query(PixEventType).filter(PixEventType.enumerator == enumerator).one()
