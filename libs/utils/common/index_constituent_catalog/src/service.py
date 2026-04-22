from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from libs.utils.common.constants.src.seeder import (
    INDEX_HEATMAP_CSV_PATH,
    SECTOR_HEATMAP_CSV_PATH,
)


@dataclass(frozen=True)
class ConstituentDefinition:
    symbol: str
    name: str
    fyers_symbol: str
    industry: str | None = None
    sector: str | None = None


class IndexConstituentCatalogService:
    _index_map: dict[str, list[ConstituentDefinition]] | None = None
    _all_symbols: dict[str, ConstituentDefinition] | None = None

    @classmethod
    def get_index_names(cls) -> list[str]:
        cls._ensure_loaded()
        return sorted(cls._index_map.keys())

    @classmethod
    def get_constituents(cls, index_name: str) -> list[ConstituentDefinition]:
        cls._ensure_loaded()
        normalized = index_name.strip().upper().replace(" ", "")
        return cls._index_map.get(normalized, [])

    @classmethod
    def get_all_unique_constituents(cls) -> list[ConstituentDefinition]:
        cls._ensure_loaded()
        return list(cls._all_symbols.values())

    @classmethod
    def get_constituent_by_symbol(cls, symbol: str) -> ConstituentDefinition | None:
        cls._ensure_loaded()
        return cls._all_symbols.get(symbol.strip().upper())

    @classmethod
    def _ensure_loaded(cls):
        if cls._index_map is not None:
            return
        index_map: dict[str, dict[str, ConstituentDefinition]] = {}
        all_symbols: dict[str, ConstituentDefinition] = {}

        cls._load_index_csv(INDEX_HEATMAP_CSV_PATH, "INDEX", index_map, all_symbols)
        cls._load_sector_csv(SECTOR_HEATMAP_CSV_PATH, index_map, all_symbols)

        # Convert inner dicts to sorted lists
        cls._index_map = {
            name: sorted(symbols.values(), key=lambda x: x.symbol)
            for name, symbols in index_map.items()
        }
        cls._all_symbols = all_symbols

    @staticmethod
    def _load_index_csv(
        path: Path,
        index_col: str,
        index_map: dict[str, dict[str, ConstituentDefinition]],
        all_symbols: dict[str, ConstituentDefinition],
    ):
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row.get("Symbol", "").strip().upper()
                if not symbol:
                    continue
                index_name = row.get(index_col, "").strip().upper().replace(" ", "")
                if not index_name:
                    continue
                if symbol not in all_symbols:
                    defn = ConstituentDefinition(
                        symbol=symbol,
                        name=row.get("Company Name", symbol),
                        fyers_symbol=f"NSE:{symbol}-EQ",
                        industry=row.get("INDUSTRY"),
                    )
                    all_symbols[symbol] = defn
                    if index_name not in index_map:
                        index_map[index_name] = {}
                    index_map[index_name][symbol] = defn
                else:
                    # Symbol already seen, just add to index mapping
                    if index_name not in index_map:
                        index_map[index_name] = {}
                    index_map[index_name][symbol] = all_symbols[symbol]

    @staticmethod
    def _load_sector_csv(
        path: Path,
        index_map: dict[str, dict[str, ConstituentDefinition]],
        all_symbols: dict[str, ConstituentDefinition],
    ):
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row.get("Symbol", "").strip().upper()
                if not symbol:
                    continue
                sector_name = row.get("SECTOR", "").strip().upper().replace(" ", "")
                if not sector_name:
                    continue
                sector_value = row.get("SECTOR", "").strip()
                if symbol not in all_symbols:
                    defn = ConstituentDefinition(
                        symbol=symbol,
                        name=row.get("Company Name", symbol),
                        fyers_symbol=f"NSE:{symbol}-EQ",
                        industry=row.get("INDUSTRY"),
                        sector=sector_value,
                    )
                    all_symbols[symbol] = defn
                else:
                    # Update existing entry with sector if missing
                    existing = all_symbols[symbol]
                    if existing.sector is None:
                        updated = ConstituentDefinition(
                            symbol=existing.symbol,
                            name=existing.name,
                            fyers_symbol=existing.fyers_symbol,
                            industry=existing.industry,
                            sector=sector_value,
                        )
                        all_symbols[symbol] = updated
                        # Also update any index_map entries pointing to old defn
                        for idx_symbols in index_map.values():
                            if (
                                symbol in idx_symbols
                                and idx_symbols[symbol] is existing
                            ):
                                idx_symbols[symbol] = updated
                if sector_name not in index_map:
                    index_map[sector_name] = {}
                index_map[sector_name][symbol] = all_symbols[symbol]
