import uuid

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from libs.utils.common.constants.src.db_collections import (
    OPTION_CHAIN_STRIKE_SUMMARIES_TABLE,
)
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class OptionChainStrikeSummary(Base, TimestampMixin):
    """
    Per-snapshot, per-strike aggregated call/put metrics used in dashboard strike table.
    """

    __tablename__ = OPTION_CHAIN_STRIKE_SUMMARIES_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("option_chain_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id = Column(
        UUID(as_uuid=True), ForeignKey("instruments.id"), nullable=False
    )
    expiry_id = Column(UUID(as_uuid=True), ForeignKey("expiries.id"), nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    strike_price = Column(Numeric, nullable=False)

    call_option_contract_id = Column(
        UUID(as_uuid=True), ForeignKey("option_contracts.id")
    )
    put_option_contract_id = Column(
        UUID(as_uuid=True), ForeignKey("option_contracts.id")
    )

    call_oi_change = Column(BigInteger, nullable=False, default=0)
    put_oi_change = Column(BigInteger, nullable=False, default=0)
    net_oi_change = Column(BigInteger, nullable=False, default=0)

    call_oi = Column(BigInteger, nullable=False, default=0)
    put_oi = Column(BigInteger, nullable=False, default=0)
    net_oi = Column(BigInteger, nullable=False, default=0)

    call_volume = Column(BigInteger, nullable=False, default=0)
    put_volume = Column(BigInteger, nullable=False, default=0)

    call_ltp = Column(Numeric)
    put_ltp = Column(Numeric)

    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "strike_price",
            name="uq_strike_summary_snapshot_strike",
        ),
        Index(
            "ix_strike_summary_instrument_captured",
            "instrument_id",
            "captured_at",
        ),
        Index("ix_strike_summary_snapshot", "snapshot_id"),
    )

    snapshot = relationship("OptionChainSnapshot")
