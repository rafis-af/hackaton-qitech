from datetime import datetime
from typing import Optional

from sqlalchemy.dialects.postgresql import insert

from database import Context
from models import DocumentSituation


class DocumentSituationRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def get_consulted_since(self, document: str, since: datetime) -> Optional[DocumentSituation]:
        return (
            self.session.query(DocumentSituation)
            .filter(
                DocumentSituation.document_number == document,
                DocumentSituation.consulted_at >= since,
            )
            .first()
        )

    def save(self, document: str, situation: str, consulted_at: datetime) -> None:
        statement = insert(DocumentSituation).values(
            document_number=document,
            situation=situation,
            consulted_at=consulted_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[DocumentSituation.document_number],
            set_={"situation": situation, "consulted_at": consulted_at},
        )
        self.session.execute(statement)
