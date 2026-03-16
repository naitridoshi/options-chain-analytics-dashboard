import json
from pathlib import Path
from typing import Any

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

    @classmethod
    async def seed_missing_instruments(
        cls, instruments: list[dict[str, Any]]
    ) -> dict[str, list[str]]:
        async with postgres_connection.get_session() as session:
            repo = get_instruments_repository(session)

            seen_symbols: set[str] = set()
            insert_payloads: list[dict[str, Any]] = []

            for instrument_data in instruments:
                symbol = instrument_data.get("symbol")
                if not symbol:
                    raise ValueError(
                        "Each instrument must include a non-empty 'symbol'."
                    )

                if symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)
                insert_payloads.append(instrument_data)

            inserted_symbol_set = await repo.bulk_insert_ignore_existing(
                insert_payloads
            )
            inserted_symbols = sorted(inserted_symbol_set)
            skipped_symbols = sorted(seen_symbols - inserted_symbol_set)

            return {
                "inserted_symbols": inserted_symbols,
                "skipped_symbols": skipped_symbols,
            }

    @classmethod
    async def seed_missing_instruments_from_file(
        cls, instruments_file_path: str | Path
    ) -> dict[str, list[str]]:
        with open(instruments_file_path, "r") as file:
            instruments = json.load(file)
        return await cls.seed_missing_instruments(instruments)
