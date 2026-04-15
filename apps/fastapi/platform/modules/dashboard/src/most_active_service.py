"""
Most Active Service - Provides top 5 most active option contracts
sorted by Volume and Open Interest for calls, puts, and combined.
Uses the same data pipeline as the dashboard: raw snapshot from Redis
with live market data merged in. No manual calculations.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.db.redis.src import (
    RedisLiveMarketStore,
    RedisOptionChainSnapshotStore,
)

IST = ZoneInfo("Asia/Kolkata")

log = CustomLogger("MostActiveService")
logger, listener = log.get_logger()
listener.start()

TOP_N = 5


def _merge_live(strikes: list[dict], live_map: dict) -> list[dict]:
    """Merge live market data into strikes - same as dashboard."""
    merged = []
    for s in strikes:
        row = dict(s)
        call_sym = row.get("call_trading_symbol")
        put_sym = row.get("put_trading_symbol")
        call_live = live_map.get(call_sym or "")
        put_live = live_map.get(put_sym or "")
        row["call_live_ltp"] = call_live.get("ltp") if call_live else None
        row["call_live_avg_price"] = call_live.get("avg_price") if call_live else None
        row["put_live_ltp"] = put_live.get("ltp") if put_live else None
        row["put_live_avg_price"] = put_live.get("avg_price") if put_live else None
        merged.append(row)
    return merged


def _extract_calls(strikes: list[dict]) -> list[dict]:
    """Extract call-side data from merged strikes."""
    rows = []
    for s in strikes:
        if not s.get("call_trading_symbol"):
            continue
        rows.append(
            {
                "strike_price": s["strike_price"],
                "type": "CE",
                "symbol": s["call_trading_symbol"],
                "ltp": s.get("call_live_ltp") or s.get("call_ltp"),
                "ltp_change": s.get("call_ltp_change"),
                "vwap": s.get("call_live_avg_price"),
                "volume": s.get("call_volume") or 0,
                "oi": s.get("call_oi") or 0,
                "oi_change": s.get("call_oi_change") or 0,
            }
        )
    return rows


def _extract_puts(strikes: list[dict]) -> list[dict]:
    """Extract put-side data from merged strikes."""
    rows = []
    for s in strikes:
        if not s.get("put_trading_symbol"):
            continue
        rows.append(
            {
                "strike_price": s["strike_price"],
                "type": "PE",
                "symbol": s["put_trading_symbol"],
                "ltp": s.get("put_live_ltp") or s.get("put_ltp"),
                "ltp_change": s.get("put_ltp_change"),
                "vwap": s.get("put_live_avg_price"),
                "volume": s.get("put_volume") or 0,
                "oi": s.get("put_oi") or 0,
                "oi_change": s.get("put_oi_change") or 0,
            }
        )
    return rows


class MostActiveService:
    @classmethod
    async def get_most_active_data(cls) -> dict:
        instrument = InstrumentCatalogService.get_by_symbol("NIFTY")
        if not instrument:
            instruments = InstrumentCatalogService.get_active_instruments()
            instrument = instruments[0] if instruments else None
        if not instrument:
            return cls._empty_payload()

        trade_date = datetime.now(IST).date().isoformat()

        try:
            latest_snapshot = await RedisOptionChainSnapshotStore.get_latest_snapshot(
                instrument_symbol=instrument.symbol,
                trade_date=trade_date,
            )
        except Exception as e:
            logger.warning(f"Failed to get latest snapshot: {e}")
            latest_snapshot = None

        if not latest_snapshot:
            return cls._empty_payload(instrument=instrument)

        strikes = latest_snapshot.get("strikes", [])
        latest = latest_snapshot.get("latest", {})
        spot_price = latest.get("spot_price")
        captured_at = latest.get("captured_at")

        # Merge live market data - same as dashboard
        all_symbols = []
        for s in strikes:
            if s.get("call_trading_symbol"):
                all_symbols.append(s["call_trading_symbol"])
            if s.get("put_trading_symbol"):
                all_symbols.append(s["put_trading_symbol"])

        live_map = {}
        if all_symbols:
            try:
                live_map = await RedisLiveMarketStore.get_live_symbols(all_symbols)
            except Exception as e:
                logger.warning(f"Failed to get live market data: {e}")

        merged = _merge_live(strikes, live_map)

        # Extract call and put rows from merged strikes
        call_rows = _extract_calls(merged)
        put_rows = _extract_puts(merged)

        # Sort and pick top N
        most_active_by_volume = sorted(
            call_rows + put_rows, key=lambda x: x["volume"], reverse=True
        )[:TOP_N]

        most_active_calls_by_oi = sorted(
            call_rows, key=lambda x: x["oi"], reverse=True
        )[:TOP_N]

        most_active_calls_by_volume = sorted(
            call_rows, key=lambda x: x["volume"], reverse=True
        )[:TOP_N]

        most_active_puts_by_oi = sorted(put_rows, key=lambda x: x["oi"], reverse=True)[
            :TOP_N
        ]

        most_active_puts_by_volume = sorted(
            put_rows, key=lambda x: x["volume"], reverse=True
        )[:TOP_N]

        return {
            "instrument": {
                "id": str(getattr(instrument, "id", instrument.symbol)),
                "symbol": instrument.symbol,
                "name": getattr(instrument, "name", None) or instrument.symbol,
            },
            "spot_price": spot_price,
            "captured_at": captured_at,
            "market_date": trade_date,
            "most_active_by_volume": most_active_by_volume,
            "most_active_calls_by_oi": most_active_calls_by_oi,
            "most_active_calls_by_volume": most_active_calls_by_volume,
            "most_active_puts_by_oi": most_active_puts_by_oi,
            "most_active_puts_by_volume": most_active_puts_by_volume,
        }

    @staticmethod
    def _empty_payload(instrument=None) -> dict:
        return {
            "instrument": {
                "id": str(getattr(instrument, "id", "NIFTY")),
                "symbol": getattr(instrument, "symbol", "NIFTY"),
                "name": getattr(instrument, "name", "NIFTY"),
            }
            if instrument
            else {"id": "NIFTY", "symbol": "NIFTY", "name": "NIFTY"},
            "spot_price": None,
            "captured_at": None,
            "market_date": datetime.now(IST).date().isoformat(),
            "most_active_by_volume": [],
            "most_active_calls_by_oi": [],
            "most_active_calls_by_volume": [],
            "most_active_puts_by_oi": [],
            "most_active_puts_by_volume": [],
        }
