import uuid

from sqlalchemy import (
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
    OPTION_CHAIN_SNAPSHOTS_TABLE,
)
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class OptionChainSnapshot(Base, TimestampMixin):
    """
    One row per snapshot capture event.
    """

    __tablename__ = OPTION_CHAIN_SNAPSHOTS_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(
        UUID(as_uuid=True), ForeignKey("instruments.id"), nullable=False
    )
    expiry_id = Column(UUID(as_uuid=True), ForeignKey("expiries.id"), nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    spot_price = Column(Numeric, nullable=False)

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "expiry_id",
            "captured_at",
            name="uq_snapshot_instrument_expiry_captured",
        ),
        Index(
            "ix_snapshot_instrument_expiry_captured",
            "instrument_id",
            "expiry_id",
            "captured_at",
        ),
    )

    # Relationships
    instrument = relationship("Instrument", back_populates="snapshots")
    expiry = relationship("Expiry", back_populates="snapshots")
    strikes = relationship(
        "OptionChainStrike",
        back_populates="snapshot",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
