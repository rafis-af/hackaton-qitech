from sqlalchemy import Boolean, Column, DateTime, Integer, SmallInteger, String, UniqueConstraint, func
from models.base import Base


class Rule(Base):
    __tablename__ = "rule"

    id = Column(Integer, primary_key=True)
    enumerator = Column(String(50), nullable=False)
    family = Column(String(20), nullable=False)
    action = Column(String(20), nullable=False)
    points = Column(SmallInteger, nullable=False)
    active = Column(Boolean, nullable=False, server_default="true")
    description = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("enumerator"),)

    SCORE = "score"
    TWO_FACTOR = "two_factor"
    REJECT = "reject"
