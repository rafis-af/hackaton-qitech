from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from models.base import Base
from models import PixEventType, PixTransaction


class PixEvent(Base):
    __tablename__ = "pix_event"

    id = Column(Integer, primary_key=True)
    pix_transaction_id = Column(Integer, ForeignKey(PixTransaction.id), nullable=False)
    event_type_id = Column(Integer, ForeignKey(PixEventType.id), nullable=False)
    request_id = Column(String(64), nullable=False)
    error_code = Column(String(10), nullable=True)
    details = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    pix_transaction = relationship("PixTransaction", foreign_keys=[pix_transaction_id], back_populates="events")
    event_type = relationship("PixEventType", foreign_keys=[event_type_id], lazy="selectin")
