from typing import Optional

from database import Context
from models import RestrictedParty


class RestrictedPartyRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def get_by_document(self, document: str) -> Optional[RestrictedParty]:
        return self.session.query(RestrictedParty).filter(RestrictedParty.document_number == document).first()
