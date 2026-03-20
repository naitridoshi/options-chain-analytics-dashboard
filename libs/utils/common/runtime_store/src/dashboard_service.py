from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.config.src.fyers import (
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    SNAPSHOT_INTERVAL_SECONDS,
)
from libs.utils.db.redis.src import (
    RedisLiveMarketStore,
    RedisOptionChainSnapshotStore,
    RedisWeeklyCloseStore,
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

        # Get current spot for movement calculations
        current_spot = latest_payload.get("spot_price")
        if current_spot is not None:
            current_spot = float(current_spot)

        # Capture weekly close if it's Tuesday after market close
        if current_spot is not None:
            await cls._capture_weekly_close_if_needed(
                instrument_symbol=instrument.symbol,
                trade_date=trade_date,
                current_spot=current_spot,
            )

        # Calculate movements
        movements = await cls._calculate_movements(
            instrument_symbol=instrument.symbol,
            trade_date=trade_date,
            current_spot=current_spot,
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
            "movements": movements,
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

    @classmethod
    async def _get_market_open_snapshot(
        cls,
        instrument_symbol: str,
        trade_date: str,
    ) -> dict | None:
        """
        Get the snapshot closest to 9:15 AM within the 9:00-9:15 window.
        This is used as the reference for 'Today's Movement from Open'.
        Fallback to first snapshot of the day if none found in window.
        """
        timeline = await RedisOptionChainSnapshotStore.get_timeline(
            instrument_symbol=instrument_symbol,
            trade_date=trade_date,
            limit=100,  # Get enough snapshots to find the open
        )
        if not timeline:
            return None

        market_open_time = time(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE)
        market_open_end_time = time(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE + 15)
        target_time = time(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE + 15)  # 9:15 AM

        # Collect all snapshots within the 9:00-9:15 window
        candidates = []
        for snapshot in reversed(
            timeline
        ):  # Timeline is in reverse order (latest first)
            latest = snapshot.get("latest") or snapshot
            captured_at_str = latest.get("captured_at")
            if not captured_at_str:
                continue

            try:
                captured_at = datetime.fromisoformat(captured_at_str)
                captured_time = captured_at.astimezone(IST).time()

                # Check if captured between market open and 15 mins after
                if market_open_time <= captured_time <= market_open_end_time:
                    # Calculate time difference from 9:15 AM in seconds
                    captured_seconds = (
                        captured_time.hour * 3600
                        + captured_time.minute * 60
                        + captured_time.second
                    )
                    target_seconds = (
                        target_time.hour * 3600
                        + target_time.minute * 60
                        + target_time.second
                    )
                    diff_seconds = abs(captured_seconds - target_seconds)

                    candidates.append(
                        {
                            "spot_price": latest.get("spot_price"),
                            "captured_at": captured_at_str,
                            "diff_seconds": diff_seconds,
                        }
                    )
            except (ValueError, TypeError):
                continue

        # If we have candidates, return the one closest to 9:15 AM
        if candidates:
            candidates.sort(key=lambda x: x["diff_seconds"])
            best = candidates[0]
            return {
                "spot_price": best["spot_price"],
                "captured_at": best["captured_at"],
            }

        # Fallback: return the earliest snapshot of the day
        if timeline:
            earliest = timeline[-1]  # Last item is the earliest
            latest = earliest.get("latest") or earliest
            return {
                "spot_price": latest.get("spot_price"),
                "captured_at": latest.get("captured_at"),
            }

        return None

    @classmethod
    async def _capture_weekly_close_if_needed(
        cls,
        instrument_symbol: str,
        trade_date: str,
        current_spot: float,
    ) -> None:
        """
        On Tuesdays near market close, capture the weekly expiry close spot price.
        This is stored for calculating 'Weekly Movement from Previous Week Close'.
        """
        try:
            trade_date_obj = datetime.strptime(trade_date, "%Y-%m-%d").date()
        except ValueError:
            return

        # Check if today is Tuesday (weekday 1)
        if trade_date_obj.weekday() != 1:
            return

        now = datetime.now(IST)
        current_time = now.time()
        market_close_time = time(MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)

        # Only capture after market close time
        if current_time < market_close_time:
            return

        # Check if we already have a weekly close for this week
        existing = await RedisWeeklyCloseStore.get_weekly_close(instrument_symbol)
        if existing:
            try:
                existing_date = datetime.strptime(
                    existing.get("expiry_date", ""), "%Y-%m-%d"
                ).date()
                if existing_date >= trade_date_obj:
                    return  # Already captured this week or later
            except (ValueError, TypeError):
                pass

        # Get the last snapshot at or after market close
        timeline = await RedisOptionChainSnapshotStore.get_timeline(
            instrument_symbol=instrument_symbol,
            trade_date=trade_date,
            limit=50,
        )

        close_snapshot = None
        for snapshot in timeline:
            latest = snapshot.get("latest") or snapshot
            captured_at_str = latest.get("captured_at")
            if not captured_at_str:
                continue

            try:
                captured_at = datetime.fromisoformat(captured_at_str)
                captured_time = captured_at.astimezone(IST).time()

                # Get snapshot at market close or the last one of the day
                if captured_time >= market_close_time:
                    close_snapshot = latest
                    break
            except (ValueError, TypeError):
                continue

        # Fallback to the latest snapshot if no close-time snapshot found
        if not close_snapshot and timeline:
            close_snapshot = timeline[0].get("latest") or timeline[0]

        if close_snapshot:
            await RedisWeeklyCloseStore.save_weekly_close(
                instrument_symbol=instrument_symbol,
                expiry_date=trade_date,
                close_spot=float(close_snapshot.get("spot_price", current_spot)),
                captured_at=close_snapshot.get("captured_at", now.isoformat()),
            )
            logger.info(
                f"Weekly close captured - instrument: {instrument_symbol} - "
                f"expiry_date: {trade_date} - close_spot: {close_snapshot.get('spot_price')}"
            )

    @classmethod
    async def _calculate_movements(
        cls,
        instrument_symbol: str,
        trade_date: str,
        current_spot: float | None,
    ) -> dict:
        """
        Calculate today's movement from open and weekly movement from previous close.
        Returns a dict with movement data for the dashboard.
        """
        movements = {
            "today_from_open": None,
            "weekly_from_close": None,
        }

        if current_spot is None:
            return movements

        # Calculate today's movement from open
        open_snapshot = await cls._get_market_open_snapshot(
            instrument_symbol, trade_date
        )
        if open_snapshot and open_snapshot.get("spot_price") is not None:
            open_spot = float(open_snapshot["spot_price"])
            if open_spot != 0:
                movement_pct = ((current_spot - open_spot) / open_spot) * 100
                movements["today_from_open"] = {
                    "open_spot": open_spot,
                    "current_spot": current_spot,
                    "movement_pct": round(movement_pct, 2),
                    "captured_at_open": open_snapshot.get("captured_at"),
                }

        # Calculate weekly movement from previous week close
        weekly_close = await RedisWeeklyCloseStore.get_weekly_close(instrument_symbol)
        if weekly_close and weekly_close.get("close_spot") is not None:
            prev_close = float(weekly_close["close_spot"])
            if prev_close != 0:
                movement_pct = ((current_spot - prev_close) / prev_close) * 100
                movements["weekly_from_close"] = {
                    "prev_week_close": prev_close,
                    "current_spot": current_spot,
                    "movement_pct": round(movement_pct, 2),
                    "expiry_date": weekly_close.get("expiry_date"),
                    "captured_at_close": weekly_close.get("captured_at"),
                }

        return movements
