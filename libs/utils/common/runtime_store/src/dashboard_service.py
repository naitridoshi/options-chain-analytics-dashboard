from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.config.src.fyers import SNAPSHOT_INTERVAL_SECONDS
from libs.utils.db.redis.src import RedisOptionChainSnapshotStore

IST = ZoneInfo("Asia/Kolkata")


class RuntimeDashboardService:
    """Reads dashboard state from Redis when runtime snapshots are available."""

    @classmethod
    async def get_dashboard_data(
        cls,
        *,
        symbol: str | None = None,
        timeline_limit: int = 100,
    ) -> dict | None:
        instrument = await cls._resolve_instrument(symbol)
        if not instrument:
            return None

        trade_date = datetime.now(IST).date().isoformat()
        latest_snapshot = await RedisOptionChainSnapshotStore.get_latest_snapshot(
            instrument_symbol=instrument.symbol,
            trade_date=trade_date,
        )
        if not latest_snapshot:
            return None

        timeline_snapshots = await RedisOptionChainSnapshotStore.get_timeline(
            instrument_symbol=instrument.symbol,
            trade_date=trade_date,
            limit=timeline_limit,
        )
        previous_day_final = (
            await RedisOptionChainSnapshotStore.get_previous_day_final_snapshot(
                instrument.symbol
            )
        )

        latest_payload = dict(latest_snapshot.get("latest") or {})
        if (
            previous_day_final
            and latest_payload
            and not latest_payload.get("prev_close_spot")
        ):
            previous_latest = previous_day_final.get("latest") or {}
            latest_payload["prev_close_spot"] = previous_latest.get("spot_price")
            latest_payload["prev_close_captured_at"] = previous_latest.get(
                "captured_at"
            )
            latest_payload["prev_close_selection"] = "previous_day_final_snapshot"

        return {
            "instrument": latest_snapshot.get("instrument")
            or {
                "id": str(instrument.id),
                "symbol": instrument.symbol,
                "name": getattr(instrument, "name", None) or instrument.symbol,
                "exchange": getattr(instrument, "exchange", None),
                "instrument_type": getattr(instrument, "instrument_type", None),
                "fyers_symbol": instrument.fyers_symbol,
            },
            "market_date": latest_snapshot.get("market_date", trade_date),
            "refresh_seconds": latest_snapshot.get(
                "refresh_seconds", SNAPSHOT_INTERVAL_SECONDS
            ),
            "timeline": [item.get("latest", item) for item in timeline_snapshots],
            "latest": latest_payload,
            "strikes": latest_snapshot.get("strikes", []),
            "source": "redis",
        }

    @classmethod
    async def _resolve_instrument(cls, symbol: str | None):
        if symbol:
            return InstrumentCatalogService.get_by_symbol(symbol.upper())

        instruments = InstrumentCatalogService.get_active_instruments()
        return instruments[0] if instruments else None
