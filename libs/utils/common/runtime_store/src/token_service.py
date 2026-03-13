from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.config.src.redis import (
    REDIS_ENABLED,
    REDIS_RUNTIME_STORE_USE_POSTGRES_FALLBACK,
    REDIS_RUNTIME_STORE_WRITE_THROUGH_POSTGRES,
)
from libs.utils.db.postgres.operations.src import FyersTokenOperations
from libs.utils.db.redis.src import RedisTokenStore, RuntimeFyersToken

log = CustomLogger("RuntimeTokenService")
logger, listener = log.get_logger()
listener.start()


@dataclass
class TokenStatusPayload:
    has_token: bool
    token_date: str
    created_at: str | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    source: str | None = None
    message: str | None = None


class RuntimeTokenService:
    @classmethod
    async def get_token_for_date(cls, token_date: date) -> RuntimeFyersToken | None:
        if REDIS_ENABLED:
            token = await RedisTokenStore.get_token_for_date(token_date)
            if token:
                return token

        if not REDIS_RUNTIME_STORE_USE_POSTGRES_FALLBACK:
            return None

        token_row = await FyersTokenOperations.get_token_for_date(token_date)
        if not token_row:
            return None

        token = RuntimeFyersToken(
            access_token=token_row.access_token,
            token_date=token_row.token_date.isoformat(),
            created_at=token_row.created_at.isoformat()
            if getattr(token_row, "created_at", None)
            else datetime.now(timezone.utc).isoformat(),
            expires_at=token_row.expires_at.isoformat()
            if getattr(token_row, "expires_at", None)
            else None,
            source="postgres",
        )
        if REDIS_ENABLED:
            await RedisTokenStore.upsert_token(
                token_date=token_date,
                access_token=token.access_token,
                expires_at=token_row.expires_at,
            )
        return token

    @classmethod
    async def get_today_token(cls) -> RuntimeFyersToken | None:
        return await cls.get_token_for_date(datetime.now(timezone.utc).date())

    @classmethod
    async def upsert_today_token(
        cls,
        *,
        access_token: str,
        expires_at: datetime | None = None,
    ) -> RuntimeFyersToken:
        today = datetime.now(timezone.utc).date()
        token = RuntimeFyersToken(
            access_token=access_token,
            token_date=today.isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=expires_at.isoformat() if expires_at else None,
            source="redis" if REDIS_ENABLED else "postgres",
        )

        if REDIS_ENABLED:
            token = await RedisTokenStore.upsert_token(
                token_date=today,
                access_token=access_token,
                expires_at=expires_at,
            )

        if REDIS_RUNTIME_STORE_WRITE_THROUGH_POSTGRES:
            await FyersTokenOperations.upsert_today_token(
                access_token=access_token,
                expires_at=expires_at,
            )

        return token

    @classmethod
    async def get_today_token_status(cls) -> TokenStatusPayload:
        token = await cls.get_today_token()
        today = datetime.now(timezone.utc).date().isoformat()
        if not token:
            return TokenStatusPayload(
                has_token=False,
                token_date=today,
                message="No token stored for today. Complete /api/v1/fyers/login.",
            )

        return TokenStatusPayload(
            has_token=True,
            token_date=token.token_date,
            created_at=token.created_at,
            expires_at=token.expires_at,
            source=token.source,
            message="FYERS token for today is available.",
        )
