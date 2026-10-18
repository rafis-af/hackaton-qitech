from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import relationship
from models.base import Base
from models import Analysis


class TwoFactorResult(Base):
    __tablename__ = "two_factor_result"

    id = Column(Integer, primary_key=True)
    analysis_id = Column(Integer, ForeignKey(Analysis.id), nullable=False)
    result = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("analysis_id"),)

    analysis = relationship("Analysis", foreign_keys=[analysis_id], back_populates="two_factor_result")

    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    EXPIRED = "expired"
