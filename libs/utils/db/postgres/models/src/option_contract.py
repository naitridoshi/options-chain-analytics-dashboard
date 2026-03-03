import uuid

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from libs.utils.common.constants.src.db_collections import (
    OPTION_CONTRACTS_TABLE,
)
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class OptionContract(Base, TimestampMixin):
    """
    Static option contract definitions (strike price, type, trading symbol).
    """

    __tablename__ = OPTION_CONTRACTS_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(
        UUID(as_uuid=True), ForeignKey("instruments.id"), nullable=False
    )
    expiry_id = Column(UUID(as_uuid=True), ForeignKey("expiries.id"), nullable=False)
    strike_price = Column(Numeric, nullable=False)
    option_type = Column(String, nullable=False)  # CE / PE
    trading_symbol = Column(String, unique=True, nullable=False)
    lot_size = Column(Integer)

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint(
            "expiry_id",
            "strike_price",
            "option_type",
            name="uq_contract_expiry_strike_type",
        ),
        Index(
            "ix_contract_expiry_strike_type", "expiry_id", "strike_price", "option_type"
        ),
    )

    # Relationships
    instrument = relationship("Instrument", back_populates="option_contracts")
    expiry = relationship("Expiry", back_populates="option_contracts")
    strikes = relationship(
        "OptionChainStrike", back_populates="option_contract", lazy="selectin"
    )
