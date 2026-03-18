from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from libs.utils.db.postgres.models.src import (
    Expiry,
    FyersToken,
    Instrument,
    OptionChainIntervalSummary,
    OptionChainSnapshot,
    OptionChainStrike,
    OptionChainStrikeSummary,
    OptionContract,
    Script,
    ScriptPriceSnapshot,
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
                OptionChainStrike.ltp_change,
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
                "ltp_change": row.ltp_change,
            }
            for row in rows
        ]


class ExpiryRepository(BaseRepository):
    async def get_or_create(
        self,
        *,
        instrument_id,
        expiry_date,
        is_weekly: bool,
    ):
        stmt = (
            pg_insert(self.model)
            .values(
                instrument_id=instrument_id,
                expiry_date=expiry_date,
                is_weekly=is_weekly,
            )
            .on_conflict_do_nothing(index_elements=["instrument_id", "expiry_date"])
        )
        await self.session.execute(stmt)
        return await self.get(
            [
                self.model.instrument_id == instrument_id,
                self.model.expiry_date == expiry_date,
            ]
        )


class FyersTokenRepository(BaseRepository):
    async def upsert_for_date(
        self,
        *,
        token_date,
        access_token: str,
        expires_at,
    ):
        stmt = (
            pg_insert(self.model)
            .values(
                token_date=token_date,
                access_token=access_token,
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                index_elements=["token_date"],
                set_={
                    "access_token": access_token,
                    "expires_at": expires_at,
                    "updated_at": func.now(),
                },
            )
        )
        await self.session.execute(stmt)
        return await self.get(self.model.token_date == token_date)


class InstrumentRepository(BaseRepository):
    async def bulk_insert_ignore_existing(self, values: list[dict]):
        if not values:
            return set()
        stmt = (
            pg_insert(self.model)
            .values(values)
            .on_conflict_do_nothing(index_elements=["symbol"])
            .returning(self.model.symbol)
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())


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


class OptionContractRepository(BaseRepository):
    async def bulk_insert_ignore_existing(self, values: list[dict]) -> None:
        if not values:
            return
        stmt = (
            pg_insert(self.model)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=["expiry_id", "strike_price", "option_type"]
            )
        )
        await self.session.execute(stmt)


class OptionChainSnapshotRepository(BaseRepository):
    async def get_or_create(
        self,
        *,
        instrument_id,
        expiry_id,
        captured_at,
        spot_price,
    ):
        stmt = (
            pg_insert(self.model)
            .values(
                instrument_id=instrument_id,
                expiry_id=expiry_id,
                captured_at=captured_at,
                spot_price=spot_price,
            )
            .on_conflict_do_nothing(
                index_elements=["instrument_id", "expiry_id", "captured_at"]
            )
            .returning(self.model.id)
        )
        result = await self.session.execute(stmt)
        created = result.scalar_one_or_none() is not None
        snapshot = await self.get(
            [
                self.model.instrument_id == instrument_id,
                self.model.expiry_id == expiry_id,
                self.model.captured_at == captured_at,
            ]
        )
        return snapshot, created


def get_expiries_repository(session):
    return ExpiryRepository(Expiry, session)


def get_fyers_tokens_repository(session):
    return FyersTokenRepository(FyersToken, session)


def get_instruments_repository(session):
    return InstrumentRepository(Instrument, session)


def get_option_chain_snapshots_repository(session):
    return BaseRepository(OptionChainSnapshot, session)


def get_option_chain_interval_summaries_repository(session):
    return OptionChainIntervalSummaryRepository(OptionChainIntervalSummary, session)


def get_option_chain_strikes_repository(session):
    return OptionChainStrikeRepository(session)


def get_option_chain_strike_summaries_repository(session):
    return OptionChainStrikeSummaryRepository(OptionChainStrikeSummary, session)


def get_option_contracts_repository(session):
    return OptionContractRepository(OptionContract, session)


def get_scripts_repository(session):
    return BaseRepository(Script, session)


def get_script_price_snapshots_repository(session):
    return BaseRepository(ScriptPriceSnapshot, session)
