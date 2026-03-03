import uuid

from sqlalchemy import Column, Date, DateTime, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from libs.utils.common.constants.src.db_collections import FYERS_TOKEN_TABLE
from libs.utils.db.postgres.models.src.base import Base, TimestampMixin


class FyersToken(Base, TimestampMixin):
    """
    Stores Fyers access tokens. One row per trading day.
    Fyers tokens are valid for a single trading day only — there is no
    refresh mechanism, so we store token_date to identify the day's token.
    """

    __tablename__ = FYERS_TOKEN_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    access_token = Column(Text, nullable=False)
    token_date = Column(Date, nullable=False)
    expires_at = Column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("token_date", name="uq_fyers_token_date"),)
