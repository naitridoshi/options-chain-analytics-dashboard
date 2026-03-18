import json
from pathlib import Path
from typing import Any

from sqlalchemy import and_

from libs.utils.db.postgres.models.src.script import Script
from libs.utils.db.postgres.operations.src.base import BaseOperations
from libs.utils.db.postgres.src.connection import postgres_connection
from libs.utils.db.postgres.src.repository import get_scripts_repository


class ScriptOperations(BaseOperations[Script]):
    @classmethod
    async def get_active_scripts(cls) -> list[Script]:
        async with postgres_connection.get_session() as session:
            repo = get_scripts_repository(session)
            instance = cls(repo)
            return await instance.find_all(
                where=and_(repo.model.is_active.is_(True)),
                limit=5000,
            )

    @classmethod
    async def seed_missing_scripts(
        cls, scripts: list[dict[str, Any]]
    ) -> dict[str, list[str]]:
        async with postgres_connection.get_session() as session:
            repo = get_scripts_repository(session)

            seen_symbols: set[str] = set()
            insert_entities: list[Script] = []
            inserted_symbols: list[str] = []
            skipped_symbols: list[str] = []

            for script_data in scripts:
                symbol = script_data.get("symbol")
                fyers_symbol = script_data.get("fyers_symbol")
                if not symbol or not fyers_symbol:
                    raise ValueError(
                        "Each script must include non-empty 'symbol' and 'fyers_symbol'."
                    )

                if symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)

                existing = await repo.get(repo.model.symbol == symbol)
                if existing:
                    skipped_symbols.append(symbol)
                    continue

                insert_entities.append(Script(**script_data))
                inserted_symbols.append(symbol)

            if insert_entities:
                await repo.add_many(insert_entities, commit=False, refresh=False)

            return {
                "inserted_symbols": inserted_symbols,
                "skipped_symbols": skipped_symbols,
            }

    @classmethod
    async def seed_missing_scripts_from_file(
        cls, scripts_file_path: str | Path
    ) -> dict[str, list[str]]:
        with open(scripts_file_path, "r") as file:
            scripts = json.load(file)
        return await cls.seed_missing_scripts(scripts)
