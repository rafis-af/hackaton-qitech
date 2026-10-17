from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func
from models.base import Base


class RestrictedParty(Base):
    __tablename__ = "restricted_party"

    id = Column(Integer, primary_key=True)
    document_number = Column(String(14), nullable=False)
    reason = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("document_number"),)
