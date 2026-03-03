import uuid

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from libs.utils.common.constants.src.db_collections import EXPIRIES_TABLE
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class Expiry(Base, TimestampMixin):
    """
    Expiry dates for option contracts, linked to an instrument.
    """

    __tablename__ = EXPIRIES_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(
        UUID(as_uuid=True), ForeignKey("instruments.id"), nullable=False
    )
    expiry_date = Column(Date, nullable=False)
    is_weekly = Column(Boolean, default=True, nullable=False)

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "expiry_date", name="uq_expiry_instrument_date"
        ),
        Index("ix_expiry_instrument_date", "instrument_id", "expiry_date"),
    )

    # Relationships
    instrument = relationship("Instrument", back_populates="expiries")
    option_contracts = relationship(
        "OptionContract", back_populates="expiry", lazy="selectin"
    )
    snapshots = relationship(
        "OptionChainSnapshot", back_populates="expiry", lazy="selectin"
    )
