from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from libs.utils.common.constants.src.seeder import INSTRUMENTS_FILE_PATH


@dataclass(frozen=True)
class InstrumentDefinition:
    symbol: str
    fyers_symbol: str | None = None
    exchange: str | None = None
    instrument_type: str | None = None
    is_active: bool = True
    name: str | None = None


class InstrumentCatalogService:
    _cache: list[InstrumentDefinition] | None = None

    @classmethod
    def get_all_instruments(cls) -> list[InstrumentDefinition]:
        if cls._cache is None:
            cls._cache = cls._load_from_file(INSTRUMENTS_FILE_PATH)
        return list(cls._cache)

    @classmethod
    def get_active_instruments(cls) -> list[InstrumentDefinition]:
        return [item for item in cls.get_all_instruments() if item.is_active]

    @classmethod
    def get_by_symbol(cls, symbol: str) -> InstrumentDefinition | None:
        normalized = symbol.strip().upper()
        for item in cls.get_all_instruments():
            if item.symbol.upper() == normalized:
                return item
        return None

    @staticmethod
    def _load_from_file(path: str | Path) -> list[InstrumentDefinition]:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return [InstrumentDefinition(**item) for item in data]
