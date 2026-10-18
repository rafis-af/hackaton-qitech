from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from models.base import Base


class DocumentSituation(Base):
    """O que a Receita respondeu sobre um documento, guardado por um tempo."""

    __tablename__ = "document_situation"

    id = Column(Integer, primary_key=True)
    document_number = Column(String(14), nullable=False)
    situation = Column(String(30), nullable=False)
    consulted_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("document_number"),)

    REGULAR = "regular"
    SUSPENDED = "suspended"
    PENDING_REGULARIZATION = "pending_regularization"
    CANCELED = "canceled"
    DECEASED = "deceased"
    NULLIFIED = "nullified"
    NOT_FOUND = "not_found"
