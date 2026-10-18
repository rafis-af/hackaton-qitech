from sqlalchemy import CHAR, Column, DateTime, ForeignKey, Integer, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from models.base import Base
from models import AnalysisStatus, PixTransaction


class Analysis(Base):
    __tablename__ = "analysis"

    id = Column(Integer, primary_key=True)
    analysis_key = Column(CHAR(36), nullable=False)
    pix_transaction_id = Column(Integer, ForeignKey(PixTransaction.id), nullable=False)
    status_id = Column(Integer, ForeignKey(AnalysisStatus.id), nullable=False)
    transaction_risk = Column(SmallInteger, nullable=False)
    inherited_risk = Column(SmallInteger, nullable=False)
    risk_score = Column(SmallInteger, nullable=False)
    decision_reason = Column(String(20), nullable=False)
    analysis_metadata = Column("metadata", JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("analysis_key"),
        UniqueConstraint("pix_transaction_id"),
    )

    pix_transaction = relationship("PixTransaction", foreign_keys=[pix_transaction_id], back_populates="analysis")
    status = relationship("AnalysisStatus", foreign_keys=[status_id], lazy="selectin")
    rule_results = relationship("RuleResult", back_populates="analysis", order_by="asc(RuleResult.id)")
    two_factor_result = relationship("TwoFactorResult", back_populates="analysis", uselist=False, lazy="selectin")

    BLOCKING_RULE = "blocking_rule"
    RISK_SCORE = "risk_score"
    TWO_FACTOR_RULE = "two_factor_rule"
