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
    SCRIPT_PRICE_SNAPSHOTS_TABLE,
)
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class ScriptPriceSnapshot(Base, TimestampMixin):
    __tablename__ = SCRIPT_PRICE_SNAPSHOTS_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    script_id = Column(UUID(as_uuid=True), ForeignKey("scripts.id"), nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    ltp = Column(Numeric, nullable=False)
    previous_close = Column(Numeric, nullable=True)
    change = Column(Numeric, nullable=True)
    change_pct = Column(Numeric, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "script_id", "captured_at", name="uq_script_snapshot_captured"
        ),
        Index("ix_script_snapshot_script_captured", "script_id", "captured_at"),
    )

    script = relationship("Script", back_populates="snapshots")
