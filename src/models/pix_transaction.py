from sqlalchemy import BigInteger, Boolean, CHAR, Column, DateTime, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import relationship
from models.base import Base


class PixTransaction(Base):
    __tablename__ = "pix_transaction"

    id = Column(Integer, primary_key=True)
    pix_id = Column(String(100), nullable=False)
    payload_hash = Column(CHAR(64), nullable=False)
    amount = Column(BigInteger, nullable=False)
    transaction_date = Column(DateTime(timezone=True), nullable=False)
    source_document = Column(String(14), nullable=False)
    source_level = Column(String(10), nullable=True)
    source_branch = Column(String(10), nullable=False)
    source_account = Column(String(20), nullable=False)
    source_opening_date = Column(DateTime(timezone=True), nullable=False)
    dest_participant = Column(CHAR(8), nullable=False)
    dest_branch = Column(String(10), nullable=False)
    dest_account = Column(String(20), nullable=False)
    dest_opening_date = Column(DateTime(timezone=True), nullable=False)
    dest_document = Column(String(14), nullable=False)
    device_id = Column(String(100), nullable=False)
    channel = Column(String(20), nullable=False)
    ip = Column(String(45), nullable=False)
    latitude = Column(Numeric(9, 6), nullable=True)
    longitude = Column(Numeric(9, 6), nullable=True)
    street_mode_active = Column(Boolean, nullable=True)
    street_mode_limit = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("pix_id"),)

    analysis = relationship("Analysis", back_populates="pix_transaction", uselist=False)
