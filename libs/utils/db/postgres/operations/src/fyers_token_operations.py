from datetime import date, datetime, timezone

from libs.utils.db.postgres.models.src.fyers_token import FyersToken
from libs.utils.db.postgres.operations.src.base import BaseOperations
from libs.utils.db.postgres.src.connection import postgres_connection
from libs.utils.db.postgres.src.repository import get_fyers_tokens_repository


class FyersTokenOperations(BaseOperations[FyersToken]):
    def __init__(self, repository):
        super().__init__(repository)

    @classmethod
    async def get_token_for_date(cls, token_date: date) -> FyersToken | None:
        async with postgres_connection.get_session() as session:
            repo = get_fyers_tokens_repository(session)
            return await repo.get(repo.model.token_date == token_date)

    @classmethod
    async def get_today_token(cls) -> FyersToken | None:
        return await cls.get_token_for_date(datetime.now(timezone.utc).date())

    @classmethod
    async def upsert_today_token(
        cls,
        access_token: str,
        expires_at: datetime | None = None,
    ) -> FyersToken:
        today = datetime.now(timezone.utc).date()
        async with postgres_connection.get_session() as session:
            repo = get_fyers_tokens_repository(session)
            existing = await repo.get(repo.model.token_date == today)
            if existing:
                return await repo.update(
                    existing,
                    {
                        "access_token": access_token,
                        "expires_at": expires_at,
                    },
                    commit=False,
                )
            token = FyersToken(
                access_token=access_token,
                token_date=today,
                expires_at=expires_at,
            )
            return await repo.add(token, commit=False, refresh=False)
