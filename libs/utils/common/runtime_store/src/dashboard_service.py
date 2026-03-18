from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.config.src.fyers import SNAPSHOT_INTERVAL_SECONDS
from libs.utils.db.redis.src import (
    RedisLiveMarketStore,
    RedisOptionChainSnapshotStore,
)

log = CustomLogger("RuntimeDashboardService")
logger, listener = log.get_logger()
listener.start()

IST = ZoneInfo("Asia/Kolkata")


class RuntimeDashboardService:
    """Reads dashboard state from Redis when runtime snapshots are available."""

    @classmethod
    async def get_dashboard_data(
        cls,
        *,
        symbol: str | None = None,
        timeline_limit: int = 100,
    ) -> dict:
        instrument = await cls._resolve_instrument(symbol)
        if not instrument:
            return cls._build_empty_payload(symbol=symbol)

        trade_date = datetime.now(IST).date().isoformat()
        latest_snapshot = await RedisOptionChainSnapshotStore.get_latest_snapshot(
            instrument_symbol=instrument.symbol,
            trade_date=trade_date,
        )
        previous_day_final = (
            await RedisOptionChainSnapshotStore.get_previous_day_final_snapshot(
                instrument.symbol
            )
        )
        live_underlying = await RedisLiveMarketStore.get_live_underlying(
            instrument.symbol
        )
        if not latest_snapshot:
            return cls._build_empty_payload(
                instrument=instrument,
                trade_date=trade_date,
                previous_day_final=previous_day_final,
                live_underlying=live_underlying,
            )

        timeline_snapshots = await RedisOptionChainSnapshotStore.get_timeline(
            instrument_symbol=instrument.symbol,
            trade_date=trade_date,
            limit=timeline_limit,
        )

        latest_payload = dict(latest_snapshot.get("latest") or {})
        if (
            previous_day_final
            and latest_payload
            and not latest_payload.get("prev_close_spot")
        ):
            previous_latest = previous_day_final.get("latest") or {}
            previous_close_spot = previous_latest.get("spot_price")
            latest_payload["prev_close_spot"] = previous_close_spot
            latest_payload["prev_close_captured_at"] = previous_latest.get(
                "captured_at"
            )
            latest_payload["prev_close_selection"] = previous_day_final.get(
                "selection", "latest_previous_market_day"
            )
            if (
                previous_close_spot is not None
                and latest_payload.get("spot_price") is not None
            ):
                change_from_prev_close = float(latest_payload["spot_price"]) - float(
                    previous_close_spot
                )
                latest_payload["change_from_prev_close"] = change_from_prev_close
                if float(previous_close_spot) != 0:
                    latest_payload["change_pct_from_prev_close"] = (
                        change_from_prev_close / float(previous_close_spot)
                    ) * 100
                else:
                    latest_payload["change_pct_from_prev_close"] = None

        if live_underlying and latest_payload:
            latest_payload["spot_price"] = live_underlying.get(
                "spot_price", latest_payload.get("spot_price")
            )
            if live_underlying.get("change_from_prev_close") is not None:
                latest_payload["change_from_prev_close"] = live_underlying.get(
                    "change_from_prev_close"
                )
            if live_underlying.get("change_pct_from_prev_close") is not None:
                latest_payload["change_pct_from_prev_close"] = live_underlying.get(
                    "change_pct_from_prev_close"
                )

        strikes = await cls._merge_live_market_fields(
            latest_snapshot.get("strikes", []),
        )

        # Log snapshot details for debugging comparison with live subscription
        strike_prices = sorted(
            [
                s.get("strike_price")
                for s in strikes
                if s.get("strike_price") is not None
            ]
        )
        snapshot_captured_at = (latest_snapshot.get("latest") or {}).get(
            "captured_at", "unknown"
        )
        logger.info(
            f"Dashboard data served - instrument: {instrument.symbol} - "
            f"strikes_count: {len(strikes)} - "
            f"captured_at: {snapshot_captured_at} - "
            f"strike_range: [{strike_prices[0] if strike_prices else 'N/A'} - {strike_prices[-1] if strike_prices else 'N/A'}]"
        )

        return {
            "instrument": latest_snapshot.get("instrument")
            or cls._serialize_instrument(instrument),
            "market_date": latest_snapshot.get("market_date", trade_date),
            "refresh_seconds": latest_snapshot.get(
                "refresh_seconds", SNAPSHOT_INTERVAL_SECONDS
            ),
            "timeline": [item.get("latest", item) for item in timeline_snapshots],
            "latest": latest_payload,
            "strikes": strikes,
            "source": "redis",
        }

    @classmethod
    async def _resolve_instrument(cls, symbol: str | None):
        if symbol:
            return InstrumentCatalogService.get_by_symbol(symbol.upper())

        instruments = InstrumentCatalogService.get_active_instruments()
        return instruments[0] if instruments else None

    @classmethod
    def _build_empty_payload(
        cls,
        *,
        symbol: str | None = None,
        instrument=None,
        trade_date: str | None = None,
        previous_day_final: dict | None = None,
        live_underlying: dict | None = None,
    ) -> dict:
        resolved_instrument = instrument
        if resolved_instrument is None and symbol:
            resolved_instrument = InstrumentCatalogService.get_by_symbol(symbol.upper())

        latest = None
        if previous_day_final or live_underlying:
            previous_latest = (previous_day_final or {}).get("latest") or {}
            latest = {
                "captured_at": None,
                "spot_price": previous_latest.get("spot_price"),
                "prev_close_spot": previous_latest.get("spot_price"),
                "prev_close_captured_at": previous_latest.get("captured_at"),
                "prev_close_selection": (previous_day_final or {}).get(
                    "selection", "latest_previous_market_day"
                ),
                "change_from_prev_close": None,
                "change_pct_from_prev_close": None,
            }
            if live_underlying:
                latest["spot_price"] = live_underlying.get(
                    "spot_price", latest["spot_price"]
                )
                if live_underlying.get("prev_close_spot") is not None:
                    latest["prev_close_spot"] = live_underlying.get("prev_close_spot")
                if live_underlying.get("change_from_prev_close") is not None:
                    latest["change_from_prev_close"] = live_underlying.get(
                        "change_from_prev_close"
                    )
                if live_underlying.get("change_pct_from_prev_close") is not None:
                    latest["change_pct_from_prev_close"] = live_underlying.get(
                        "change_pct_from_prev_close"
                    )

        payload = {
            "instrument": cls._serialize_instrument(resolved_instrument)
            if resolved_instrument
            else None,
            "market_date": trade_date or datetime.now(IST).date().isoformat(),
            "refresh_seconds": SNAPSHOT_INTERVAL_SECONDS,
            "timeline": [],
            "latest": latest,
            "strikes": [],
            "source": "redis",
        }
        if previous_day_final:
            payload["previous_day_final"] = previous_day_final.get("latest")
        return payload

    @staticmethod
    def _serialize_instrument(instrument) -> dict | None:
        if not instrument:
            return None
        return {
            "id": str(getattr(instrument, "id", instrument.symbol)),
            "symbol": instrument.symbol,
            "name": getattr(instrument, "name", None) or instrument.symbol,
            "exchange": getattr(instrument, "exchange", None),
            "instrument_type": getattr(instrument, "instrument_type", None),
            "fyers_symbol": instrument.fyers_symbol,
        }

    @staticmethod
    async def _merge_live_market_fields(strikes: list[dict]) -> list[dict]:
        trading_symbols: list[str] = []
        for strike in strikes:
            call_symbol = strike.get("call_trading_symbol")
            put_symbol = strike.get("put_trading_symbol")
            if call_symbol:
                trading_symbols.append(call_symbol)
            if put_symbol:
                trading_symbols.append(put_symbol)

        live_payloads = await RedisLiveMarketStore.get_live_symbols(trading_symbols)

        merged_rows: list[dict] = []
        for strike in strikes:
            row = dict(strike)
            call_symbol = row.get("call_trading_symbol")
            put_symbol = row.get("put_trading_symbol")
            call_live = live_payloads.get(call_symbol or "")
            put_live = live_payloads.get(put_symbol or "")
            row["call_live_ltp"] = call_live.get("ltp") if call_live else None
            row["call_live_avg_price"] = (
                call_live.get("avg_price") if call_live else None
            )
            row["put_live_ltp"] = put_live.get("ltp") if put_live else None
            row["put_live_avg_price"] = put_live.get("avg_price") if put_live else None
            merged_rows.append(row)
        return merged_rows
