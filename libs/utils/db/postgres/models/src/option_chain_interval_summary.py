import uuid

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from libs.utils.common.constants.src.db_collections import (
    OPTION_CHAIN_INTERVAL_SUMMARIES_TABLE,
)
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class OptionChainIntervalSummary(Base, TimestampMixin):
    """
    Per-snapshot aggregated metrics used by dashboard interval timeline.
    """

    __tablename__ = OPTION_CHAIN_INTERVAL_SUMMARIES_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("option_chain_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    instrument_id = Column(
        UUID(as_uuid=True), ForeignKey("instruments.id"), nullable=False
    )
    expiry_id = Column(UUID(as_uuid=True), ForeignKey("expiries.id"), nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    spot_price = Column(Numeric, nullable=False)

    call_oi_change_sum = Column(BigInteger, nullable=False, default=0)
    put_oi_change_sum = Column(BigInteger, nullable=False, default=0)
    net_oi_change_sum = Column(BigInteger, nullable=False, default=0)

    call_oi_sum = Column(BigInteger, nullable=False, default=0)
    put_oi_sum = Column(BigInteger, nullable=False, default=0)
    net_oi_sum = Column(BigInteger, nullable=False, default=0)

    call_volume_sum = Column(BigInteger, nullable=False, default=0)
    put_volume_sum = Column(BigInteger, nullable=False, default=0)

    pcr_oi = Column(Numeric)
    pcr_oi_change = Column(Numeric)
    call_oi_share_pct = Column(Numeric)
    put_oi_share_pct = Column(Numeric)
    call_oi_change_share_pct = Column(Numeric)
    put_oi_change_share_pct = Column(Numeric)

    __table_args__ = (
        Index(
            "ix_interval_summary_instrument_captured",
            "instrument_id",
            "captured_at",
        ),
        Index("ix_interval_summary_snapshot", "snapshot_id"),
    )

    snapshot = relationship("OptionChainSnapshot")
