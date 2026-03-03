from sqlalchemy import insert

from libs.utils.db.postgres.models.src import (
    Expiry,
    FyersToken,
    Instrument,
    OptionChainSnapshot,
    OptionChainStrike,
    OptionContract,
)
from libs.utils.db.postgres.src.base_repository import BaseRepository


class OptionChainStrikeRepository(BaseRepository):
    def __init__(self, session):
        super().__init__(OptionChainStrike, session)

    async def bulk_insert(self, values: list[dict], *, commit: bool = False):
        if not values:
            return
        await self.session.execute(insert(self.model), values)
        if commit:
            await self.session.commit()


def get_expiries_repository(session):
    return BaseRepository(Expiry, session)


def get_fyers_tokens_repository(session):
    return BaseRepository(FyersToken, session)


def get_instruments_repository(session):
    return BaseRepository(Instrument, session)


def get_option_chain_snapshots_repository(session):
    return BaseRepository(OptionChainSnapshot, session)


def get_option_chain_strikes_repository(session):
    return OptionChainStrikeRepository(session)


def get_option_contracts_repository(session):
    return BaseRepository(OptionContract, session)
