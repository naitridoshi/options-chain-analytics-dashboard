from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from libs.utils.common.constants.src.seeder import INDICES_FILE_PATH


@dataclass(frozen=True)
class IndexDefinition:
    symbol: str
    fyers_symbol: str
    category: str
    name: str | None = None
    exchange: str | None = None
    is_active: bool = True


class IndexCatalogService:
    _cache: list[IndexDefinition] | None = None

    @classmethod
    def get_all_indices(cls) -> list[IndexDefinition]:
        if cls._cache is None:
            cls._cache = cls._load_from_file(INDICES_FILE_PATH)
        return list(cls._cache)

    @classmethod
    def get_active_indices(cls) -> list[IndexDefinition]:
        return [item for item in cls.get_all_indices() if item.is_active]

    @classmethod
    def get_by_category(cls, category: str) -> list[IndexDefinition]:
        normalized = category.strip().upper()
        return [
            item
            for item in cls.get_active_indices()
            if item.category.upper() == normalized
        ]

    @classmethod
    def get_by_symbol(cls, symbol: str) -> IndexDefinition | None:
        normalized = symbol.strip().upper()
        for item in cls.get_all_indices():
            if item.symbol.upper() == normalized:
                return item
        return None

    @classmethod
    def get_categories(cls) -> list[str]:
        return list(dict.fromkeys(item.category for item in cls.get_active_indices()))

    @staticmethod
    def _load_from_file(path: str | Path) -> list[IndexDefinition]:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return [IndexDefinition(**item) for item in data]
