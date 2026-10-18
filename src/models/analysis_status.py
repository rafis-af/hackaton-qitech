from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func
from models.base import Base


class AnalysisStatus(Base):
    __tablename__ = "analysis_status"

    id = Column(Integer, primary_key=True)
    enumerator = Column(String(50), nullable=False)
    description = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("enumerator"),)

    APPROVED = "approved"
    TWO_FACTOR_REQUIRED = "two_factor_required"
    REJECTED = "rejected"
