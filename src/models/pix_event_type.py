from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func
from models.base import Base


class PixEventType(Base):
    __tablename__ = "pix_event_type"

    id = Column(Integer, primary_key=True)
    enumerator = Column(String(40), nullable=False)
    description = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("enumerator"),)

    RECEITA_UNAVAILABLE = "receita_unavailable"
    ANALYSIS_CREATED = "analysis_created"
    REPLAY_RETURNED = "replay_returned"
    CONFLICT_REFUSED = "conflict_refused"
    TWO_FACTOR_ANSWERED = "two_factor_answered"
    TWO_FACTOR_EXPIRED = "two_factor_expired"
    TWO_FACTOR_REFUSED = "two_factor_refused"
