import uuid

from sqlalchemy import BigInteger, Column, ForeignKey, Index, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from libs.utils.common.constants.src.db_collections import (
    OPTION_CHAIN_STRIKES_TABLE,
)
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class OptionChainStrike(Base, TimestampMixin):
    """
    Per-strike data within a snapshot. This is the heavy data table.
    """

    __tablename__ = OPTION_CHAIN_STRIKES_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("option_chain_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    option_contract_id = Column(
        UUID(as_uuid=True), ForeignKey("option_contracts.id"), nullable=False
    )

    ltp = Column(Numeric)
    volume = Column(BigInteger)
    open_interest = Column(BigInteger)
    oi_change = Column(BigInteger)
    implied_volatility = Column(Numeric)

    bid_price = Column(Numeric)
    bid_qty = Column(BigInteger)
    ask_price = Column(Numeric)
    ask_qty = Column(BigInteger)

    # Indexes
    __table_args__ = (
        Index("ix_strike_snapshot_id", "snapshot_id"),
        Index("ix_strike_option_contract_id", "option_contract_id"),
    )

    snapshot = relationship("OptionChainSnapshot", back_populates="strikes")
    option_contract = relationship("OptionContract", back_populates="strikes")
