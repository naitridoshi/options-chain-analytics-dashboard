import uuid

from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from libs.utils.common.constants.src.db_collections import SCRIPTS_TABLE
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class Script(Base, TimestampMixin):
    __tablename__ = SCRIPTS_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, unique=True, nullable=False)
    fyers_symbol = Column(String, unique=True, nullable=False)
    exchange = Column(String, nullable=False, default="NSE")
    instrument_type = Column(String, nullable=False, default="EQUITY")
    lot_size = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    snapshots = relationship(
        "ScriptPriceSnapshot", back_populates="script", lazy="selectin"
    )
