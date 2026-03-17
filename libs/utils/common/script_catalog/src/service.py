from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from libs.utils.common.constants.src.seeder import SCRIPTS_FILE_PATH


@dataclass(frozen=True)
class ScriptDefinition:
    symbol: str
    fyers_symbol: str
    exchange: str | None = None
    instrument_type: str | None = None
    is_active: bool = True
    name: str | None = None
    lot_size: int | None = None


class ScriptCatalogService:
    _cache: list[ScriptDefinition] | None = None

    @classmethod
    def get_all_scripts(cls) -> list[ScriptDefinition]:
        if cls._cache is None:
            cls._cache = cls._load_from_file(SCRIPTS_FILE_PATH)
        return list(cls._cache)

    @classmethod
    def get_active_scripts(cls) -> list[ScriptDefinition]:
        return [item for item in cls.get_all_scripts() if item.is_active]

    @classmethod
    def get_by_symbol(cls, symbol: str) -> ScriptDefinition | None:
        normalized = symbol.strip().upper()
        for item in cls.get_all_scripts():
            if item.symbol.upper() == normalized:
                return item
        return None

    @staticmethod
    def _load_from_file(path: str | Path) -> list[ScriptDefinition]:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return [ScriptDefinition(**item) for item in data]
