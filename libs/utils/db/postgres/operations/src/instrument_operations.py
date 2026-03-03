from sqlalchemy import and_

from libs.utils.db.postgres.models.src.instrument import Instrument
from libs.utils.db.postgres.operations.src.base import BaseOperations
from libs.utils.db.postgres.src.connection import postgres_connection
from libs.utils.db.postgres.src.repository import get_instruments_repository


class InstrumentOperations(BaseOperations[Instrument]):
    def __init__(self, repository):
        super().__init__(repository)

    @classmethod
    async def get_active_instruments(cls) -> list[Instrument]:
        async with postgres_connection.get_session() as session:
            repo = get_instruments_repository(session)
            instance = cls(repo)
            return await instance.find_all(
                where=and_(repo.model.is_active.is_(True)),
                limit=1000,
            )

    @classmethod
    async def get_by_symbol(cls, symbol: str) -> Instrument | None:
        async with postgres_connection.get_session() as session:
            repo = get_instruments_repository(session)
            return await repo.get(repo.model.symbol == symbol)
