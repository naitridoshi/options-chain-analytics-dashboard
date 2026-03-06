import uuid

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from libs.utils.common.constants.src.db_collections import INSTRUMENTS_TABLE
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class Instrument(Base, TimestampMixin):
    """
    Configurable instruments to track (e.g. NIFTY, BANKNIFTY).
    """

    __tablename__ = INSTRUMENTS_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, unique=True, nullable=False)  # e.g. NIFTY
    fyers_symbol = Column(String, nullable=True)  # e.g. NSE:NIFTY50-INDEX
    exchange = Column(String, nullable=False)  # e.g. NSE
    instrument_type = Column(String, nullable=False)  # e.g. INDEX
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    expiries = relationship("Expiry", back_populates="instrument", lazy="selectin")
    option_contracts = relationship(
        "OptionContract", back_populates="instrument", lazy="selectin"
    )
    snapshots = relationship(
        "OptionChainSnapshot", back_populates="instrument", lazy="selectin"
    )
