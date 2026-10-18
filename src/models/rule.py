from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import relationship
from models.base import Base
from models import RiskMetric


class Rule(Base):
    __tablename__ = "rule"

    id = Column(Integer, primary_key=True)
    enumerator = Column(String(50), nullable=False)
    risk_metric_id = Column(Integer, ForeignKey(RiskMetric.id), nullable=True)
    action = Column(String(20), nullable=False)
    points = Column(SmallInteger, nullable=False)
    active = Column(Boolean, nullable=False, server_default="true")
    description = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("enumerator"),)

    risk_metric = relationship("RiskMetric", foreign_keys=[risk_metric_id], lazy="selectin")

    SCORE = "score"
    REJECT = "reject"
