from sqlalchemy import insert, select

from libs.utils.db.postgres.models.src import (
    Expiry,
    FyersToken,
    Instrument,
    OptionChainIntervalSummary,
    OptionChainSnapshot,
    OptionChainStrike,
    OptionChainStrikeSummary,
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

    async def get_summary_rows_for_snapshot(self, snapshot_id):
        stmt = (
            select(
                OptionChainStrike.option_contract_id,
                OptionContract.option_type,
                OptionContract.strike_price,
                OptionChainStrike.oi_change,
                OptionChainStrike.open_interest,
                OptionChainStrike.volume,
                OptionChainStrike.ltp,
            )
            .join(
                OptionContract,
                OptionContract.id == OptionChainStrike.option_contract_id,
            )
            .where(OptionChainStrike.snapshot_id == snapshot_id)
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        return [
            {
                "option_contract_id": row.option_contract_id,
                "option_type": row.option_type,
                "strike_price": row.strike_price,
                "oi_change": row.oi_change,
                "open_interest": row.open_interest,
                "volume": row.volume,
                "ltp": row.ltp,
            }
            for row in rows
        ]


class OptionChainIntervalSummaryRepository(BaseRepository):
    async def get_existing_snapshot_ids(self, snapshot_ids: list):
        if not snapshot_ids:
            return set()
        stmt = select(self.model.snapshot_id).where(
            self.model.snapshot_id.in_(snapshot_ids)
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())


class OptionChainStrikeSummaryRepository(BaseRepository):
    async def get_existing_snapshot_ids(self, snapshot_ids: list):
        if not snapshot_ids:
            return set()
        stmt = select(self.model.snapshot_id).where(
            self.model.snapshot_id.in_(snapshot_ids)
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())


def get_expiries_repository(session):
    return BaseRepository(Expiry, session)


def get_fyers_tokens_repository(session):
    return BaseRepository(FyersToken, session)


def get_instruments_repository(session):
    return BaseRepository(Instrument, session)


def get_option_chain_snapshots_repository(session):
    return BaseRepository(OptionChainSnapshot, session)


def get_option_chain_interval_summaries_repository(session):
    return OptionChainIntervalSummaryRepository(OptionChainIntervalSummary, session)


def get_option_chain_strikes_repository(session):
    return OptionChainStrikeRepository(session)


def get_option_chain_strike_summaries_repository(session):
    return OptionChainStrikeSummaryRepository(OptionChainStrikeSummary, session)


def get_option_contracts_repository(session):
    return BaseRepository(OptionContract, session)
