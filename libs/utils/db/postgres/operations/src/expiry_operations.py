from datetime import date

from libs.utils.db.postgres.models.src.expiry import Expiry
from libs.utils.db.postgres.operations.src.base import BaseOperations
from libs.utils.db.postgres.src.connection import postgres_connection
from libs.utils.db.postgres.src.repository import get_expiries_repository


class ExpiryOperations(BaseOperations[Expiry]):
    def __init__(self, repository):
        super().__init__(repository)

    @classmethod
    async def get_or_create(
        cls,
        instrument_id,
        expiry_date: date,
        is_weekly: bool = True,
    ) -> Expiry:
        async with postgres_connection.get_session() as session:
            repo = get_expiries_repository(session)
            existing = await repo.get(
                [
                    repo.model.instrument_id == instrument_id,
                    repo.model.expiry_date == expiry_date,
                ]
            )
            if existing:
                return existing
            entity = Expiry(
                instrument_id=instrument_id,
                expiry_date=expiry_date,
                is_weekly=is_weekly,
            )
            return await repo.add(entity, commit=False, refresh=False)
