from sqlalchemy import Column, DateTime, ForeignKey, Integer, SmallInteger, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from models.base import Base
from models import Analysis, Rule


class RuleResult(Base):
    __tablename__ = "rule_result"

    id = Column(Integer, primary_key=True)
    analysis_id = Column(Integer, ForeignKey(Analysis.id), nullable=False)
    rule_id = Column(Integer, ForeignKey(Rule.id), nullable=False)
    points = Column(SmallInteger, nullable=False)
    observed = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("analysis_id", "rule_id"),)

    analysis = relationship("Analysis", foreign_keys=[analysis_id], back_populates="rule_results")
    rule = relationship("Rule", foreign_keys=[rule_id], lazy="selectin")
