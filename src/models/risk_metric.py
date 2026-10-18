from sqlalchemy import Column, DateTime, Integer, SmallInteger, String, UniqueConstraint, func
from models.base import Base


class RiskMetric(Base):
    """O que o motor mede em cada Pix, e quanto essa medida pode pesar no máximo."""

    __tablename__ = "risk_metric"

    id = Column(Integer, primary_key=True)
    enumerator = Column(String(30), nullable=False)
    label = Column(String(30), nullable=False)
    description = Column(String(255), nullable=False)
    max_points = Column(SmallInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("enumerator"),)
