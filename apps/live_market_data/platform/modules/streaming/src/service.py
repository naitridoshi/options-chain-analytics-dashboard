from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from time import monotonic
from zoneinfo import ZoneInfo

from fyers_apiv3.FyersWebsocket import data_ws

from libs.platform.modules.option_chain_snapshot.src import (
    is_market_open_now,
    parse_expiry_candidates,
    parse_option_rows,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.config.src.fyers import (
    FYERS_APP_ID,
    FYERS_LOG_PATH,
    LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS,
    LIVE_DATA_SYMBOL_REFRESH_INTERVAL_SECONDS,
    SNAPSHOT_STRIKE_COUNT,
)
from libs.utils.db.redis.src import (
    RedisLiveMarketStore,
    RedisOptionChainSnapshotStore,
    RedisWeeklyCloseStore,
)

log = CustomLogger("LiveMarketStreamingService")
logger, listener = log.get_logger()
listener.start()
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class SubscribedOption:
    instrument_symbol: str
    strike_price: str
    option_type: str
    trading_symbol: str


@dataclass
class UnderlyingSubscription:
    instrument_symbol: str
    symbol: str
    prev_close_spot: float | None
    market_open_spot: float | None = None
    market_open_captured_at: str | None = None
    weekly_close_spot: float | None = None
    weekly_close_expiry_date: str | None = None
    weekly_close_captured_at: str | None = None


class LiveMarketStreamingService:
    _MARKET_STATE_POLL_SECONDS = 5

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._ws_client: data_ws.FyersDataSocket | None = None
        self._ws_thread: threading.Thread | None = None
        self._subscription_map: dict[str, SubscribedOption] = {}
        self._normalized_subscription_map: dict[str, SubscribedOption | None] = {}
        self._underlying_map: dict[str, UnderlyingSubscription] = {}
        self._current_symbols: list[str] = []
        self._is_connected = False
        self._last_tick_at: str | None = None
        self._last_ws_error_log_at = 0.0
        self._suppressed_ws_error_count = 0
        self._last_redis_error_log_at = 0.0
        self._suppressed_redis_error_count = 0
        self._redis_failure_count = 0
        self._redis_retry_after = 0.0
        self._redis_degraded = False
        self._next_symbol_refresh_at = 0.0
        self._market_closed_logged = False
        self._unmapped_ws_symbols_logged: set[str] = set()
        # Thread-safe lock for subscription map access during refresh
        self._subscription_lock = threading.RLock()
        # Debug tracking for symbol lookup issues
        self._tick_debug_logged_symbols: set[str] = set()
        self._symbol_lookup_mismatches: set[str] = set()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._subscription_refresh_loop())
        if is_market_open_now():
            try:
                await self._refresh_subscriptions(force_restart=True)
            except Exception as error:
                await self._handle_refresh_error(error, during_startup=True)
        else:
            self._market_closed_logged = True
            logger.info(
                "Live market streaming startup skipped - market closed, waiting for market open"
            )
        logger.info("Live market streaming service started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._ws_client:
            self._ws_client.keep_running = False
            self._ws_client = None
        self._ws_thread = None
        self._is_connected = False
        logger.info("Live market streaming service stopped")

    async def get_status(self) -> dict:
        return {
            "running": self._running,
            "subscribed_symbols": len(self._current_symbols),
            "connected": self._is_connected,
            "last_tick_at": self._last_tick_at,
        }

    async def _subscription_refresh_loop(self) -> None:
        while self._running:
            try:
                if not is_market_open_now():
                    self._pause_socket(clear_symbols=True)
                    if not self._market_closed_logged:
                        logger.info(
                            "Live market streaming paused - market closed, websocket disconnected"
                        )
                        self._market_closed_logged = True
                    await asyncio.sleep(self._MARKET_STATE_POLL_SECONDS)
                    continue

                self._market_closed_logged = False
                now = monotonic()
                force_restart = self._ws_client is None or not self._current_symbols
                if force_restart or now >= self._next_symbol_refresh_at:
                    await self._refresh_subscriptions(force_restart=force_restart)
                    self._next_symbol_refresh_at = (
                        monotonic() + LIVE_DATA_SYMBOL_REFRESH_INTERVAL_SECONDS
                    )
                await asyncio.sleep(self._MARKET_STATE_POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._handle_refresh_error(error, during_startup=False)
                await asyncio.sleep(5)

    async def _refresh_subscriptions(self, *, force_restart: bool) -> None:
        instruments = InstrumentCatalogService.get_active_instruments()
        subscription_map: dict[str, SubscribedOption] = {}
        underlying_map: dict[str, UnderlyingSubscription] = {}
        all_symbols: list[str] = []
        collision_symbols: list[str] = []  # Track collisions for logging

        for instrument in instruments:
            if not instrument.fyers_symbol:
                continue
            prev_close_spot = await self._get_previous_close_spot(instrument.symbol)
            market_open_data = await self._get_market_open_data(instrument.symbol)
            weekly_close_data = await self._get_weekly_close_data(instrument.symbol)
            underlying_map[_normalize_symbol_key(instrument.fyers_symbol)] = (
                UnderlyingSubscription(
                    instrument_symbol=instrument.symbol,
                    symbol=instrument.fyers_symbol,
                    prev_close_spot=prev_close_spot,
                    market_open_spot=market_open_data.get("spot_price"),
                    market_open_captured_at=market_open_data.get("captured_at"),
                    weekly_close_spot=weekly_close_data.get("close_spot"),
                    weekly_close_expiry_date=weekly_close_data.get("expiry_date"),
                    weekly_close_captured_at=weekly_close_data.get("captured_at"),
                )
            )
            all_symbols.append(instrument.fyers_symbol)

            rows = await self._get_snapshot_subscription_rows(instrument.symbol)
            if not rows:
                chain_data = await FyersClientService.fetch_option_chain(
                    symbol=instrument.fyers_symbol,
                    strike_count=SNAPSHOT_STRIKE_COUNT,
                )
                expiry_candidates = parse_expiry_candidates(chain_data)
                if not expiry_candidates:
                    continue
                nearest_expiry = expiry_candidates[0]["expiry_date"]
                rows = [
                    row
                    for row in parse_option_rows(chain_data)
                    if row["expiry_date"] == nearest_expiry
                ]
                rows = _dedupe_rows_by_strike_and_option_type(rows)

            for row in rows:
                trading_symbol = row.get("trading_symbol")
                strike_price = row.get("strike_price")
                option_type = row.get("option_type")
                if not trading_symbol or strike_price is None or not option_type:
                    continue
                strike_key = _normalize_strike_key(strike_price)

                # Check for duplicate exact symbol (critical error)
                if trading_symbol in subscription_map:
                    existing = subscription_map[trading_symbol]
                    logger.error(
                        "DUPLICATE SYMBOL: exact trading symbol already exists - "
                        f"symbol: {trading_symbol} - "
                        f"existing_strike: {existing.strike_price} existing_type: {existing.option_type} - "
                        f"new_strike: {strike_key} new_type: {option_type} - "
                        f"instrument: {instrument.symbol}"
                    )
                    continue  # Skip duplicate

                # Check for case-insensitive collision (potential bug source)
                normalized_key = _normalize_symbol_key(trading_symbol)
                for existing_symbol, existing_opt in list(subscription_map.items()):
                    if (
                        _normalize_symbol_key(existing_symbol) == normalized_key
                        and existing_symbol != trading_symbol
                    ):
                        logger.error(
                            f"CASE COLLISION: two symbols normalize to same key - "
                            f"symbol1: {existing_symbol} (strike: {existing_opt.strike_price}) - "
                            f"symbol2: {trading_symbol} (strike: {strike_key}) - "
                            f"normalized: {normalized_key}"
                        )
                        collision_symbols.append(normalized_key)

                # Check for same strike+type with different symbols (should not happen)
                for existing_symbol, existing_opt in subscription_map.items():
                    if (
                        existing_opt.strike_price == strike_key
                        and existing_opt.option_type == option_type
                        and existing_symbol != trading_symbol
                    ):
                        logger.error(
                            f"STRIKE COLLISION: same strike+type has different symbols - "
                            f"strike: {strike_key} type: {option_type} - "
                            f"symbol1: {existing_symbol} symbol2: {trading_symbol} - "
                            f"instrument: {instrument.symbol}"
                        )

                subscribed_option = SubscribedOption(
                    instrument_symbol=instrument.symbol,
                    strike_price=strike_key,
                    option_type=option_type,
                    trading_symbol=trading_symbol,
                )
                subscription_map[trading_symbol] = subscribed_option
                all_symbols.append(trading_symbol)

        all_symbols = sorted(set(all_symbols))
        symbols_changed = all_symbols != self._current_symbols

        # Log subscription map integrity for debugging
        logger.info(
            f"Subscription refresh - option_symbols: {len(subscription_map)}, "
            f"underlying_symbols: {len(underlying_map)}, total_subscribe: {len(all_symbols)}"
        )

        # Log all subscribed strike prices for debugging comparison with frontend
        subscribed_strikes = sorted(
            set(opt.strike_price for opt in subscription_map.values())
        )
        logger.info(
            f"Subscription strikes - count: {len(subscribed_strikes)} - "
            f"strikes: {subscribed_strikes}"
        )

        # Log any collision symbols found during build
        if collision_symbols:
            logger.error(
                f"CRITICAL: {len(collision_symbols)} symbol collision(s) detected - "
                f"collisions: {collision_symbols[:5]}{'...' if len(collision_symbols) > 5 else ''} - "
                "this may cause duplicate LTP issues"
            )

        # Verify subscription map integrity: check for strike+type collisions
        strike_type_map: dict[str, list[str]] = {}
        for sym, opt in subscription_map.items():
            key = f"{opt.strike_price}|{opt.option_type}"
            if key not in strike_type_map:
                strike_type_map[key] = []
            strike_type_map[key].append(sym)

        for key, symbols_at_key in strike_type_map.items():
            if len(symbols_at_key) > 1:
                logger.error(
                    f"SUBSCRIPTION INTEGRITY ERROR: Multiple symbols map to same strike+type - "
                    f"strike|type: {key} - symbols: {symbols_at_key} - "
                    f"This will cause LTP cross-contamination!"
                )

        # Thread-safe atomic replacement of subscription maps
        with self._subscription_lock:
            self._subscription_map = subscription_map
            self._underlying_map = underlying_map
        self._unmapped_ws_symbols_logged.clear()
        self._tick_debug_logged_symbols.clear()
        self._symbol_lookup_mismatches.clear()

        if not all_symbols:
            logger.warning("No symbols available for live websocket subscription")
            return

        if self._ws_client is None or force_restart or symbols_changed:
            await self._restart_socket(all_symbols)

    async def _restart_socket(self, symbols: list[str]) -> None:
        access_token = await FyersClientService.get_valid_access_token()
        self._current_symbols = symbols

        if self._ws_client:
            self._ws_client.keep_running = False
            self._ws_client = None

        self._ws_client = data_ws.FyersDataSocket(
            access_token=f"{FYERS_APP_ID}:{access_token}",
            log_path=FYERS_LOG_PATH,
            litemode=False,
            write_to_file=False,
            reconnect=False,
            on_connect=self._on_connect,
            on_close=self._on_close,
            on_error=self._on_error,
            on_message=self._on_message,
        )
        self._ws_thread = threading.Thread(target=self._ws_client.connect, daemon=True)
        self._ws_thread.start()
        logger.info(f"Live websocket started - symbols: {len(symbols)}")

    async def _handle_refresh_error(
        self,
        error: Exception,
        *,
        during_startup: bool,
    ) -> None:
        if _is_fyers_auth_error(error):
            self._pause_socket()
            context = "startup" if during_startup else "refresh"
            if _is_missing_daily_token_error(error):
                logger.warning(
                    "FYERS daily token unavailable, live streaming waiting for authentication - "
                    f"context: {context} - error: {str(error)}"
                )
                return
            logger.error(
                "FYERS authentication failed, live streaming paused until a valid token is available - "
                f"context: {context} - error: {str(error)}"
            )
            return

        logger.error(f"Live subscription refresh failed - error: {str(error)}")

    def _pause_socket(self, *, clear_symbols: bool = False) -> None:
        if self._ws_client:
            self._ws_client.keep_running = False
            self._ws_client = None
        self._ws_thread = None
        self._is_connected = False
        if clear_symbols:
            self._current_symbols = []

    def _on_connect(self) -> None:
        self._is_connected = True
        if self._ws_client and self._current_symbols:
            self._ws_client.subscribe(
                symbols=self._current_symbols,
                data_type="SymbolUpdate",
            )
            logger.info(
                f"Live websocket connected - subscribed_symbols: {len(self._current_symbols)}"
            )

    def _on_close(self) -> None:
        self._pause_socket()
        self._is_connected = False
        _log_throttled(
            level="warning",
            message="Live websocket disconnected",
            last_logged_at_attr="_last_ws_error_log_at",
            suppressed_count_attr="_suppressed_ws_error_count",
            owner=self,
            window_seconds=10,
        )

    def _on_error(self, error: Exception) -> None:
        self._pause_socket()
        self._is_connected = False
        _log_throttled(
            level="error",
            message=f"Live websocket error - error: {str(error)}",
            last_logged_at_attr="_last_ws_error_log_at",
            suppressed_count_attr="_suppressed_ws_error_count",
            owner=self,
            window_seconds=10,
        )

    def _on_message(self, message: dict) -> None:
        if not isinstance(message, dict):
            return

        symbol = message.get("symbol")
        ltp = message.get("ltp")
        if not symbol or not self._loop:
            return
        normalized_symbol = _normalize_symbol_key(symbol)

        # Thread-safe read from subscription maps
        with self._subscription_lock:
            underlying_subscription = self._underlying_map.get(normalized_symbol)
            if underlying_subscription:
                payload = _build_underlying_payload(
                    instrument_symbol=underlying_subscription.instrument_symbol,
                    symbol=underlying_subscription.symbol,
                    spot_price=ltp,
                    prev_close_spot=underlying_subscription.prev_close_spot,
                    market_open_spot=underlying_subscription.market_open_spot,
                    market_open_captured_at=underlying_subscription.market_open_captured_at,
                    weekly_close_spot=underlying_subscription.weekly_close_spot,
                    weekly_close_expiry_date=underlying_subscription.weekly_close_expiry_date,
                    weekly_close_captured_at=underlying_subscription.weekly_close_captured_at,
                    stale_after_seconds=LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS,
                )
                self._last_tick_at = payload["last_update"]
                self._schedule_live_write(
                    lambda p=payload, inst=underlying_subscription.instrument_symbol: (
                        RedisLiveMarketStore.write_live_underlying(
                            instrument_symbol=inst,
                            payload=p,
                        )
                    )
                )
                return

            # Primary lookup: exact symbol match ONLY (no fallback to avoid cross-contamination)
            subscribed_option = self._subscription_map.get(symbol)
            lookup_method = "exact"

            # REMOVED: case-insensitive fallback lookup
            # This was causing cross-contamination when symbols normalized to the same key
            # If exact match fails, log warning and skip - do NOT use fallback

        if not subscribed_option:
            if symbol not in self._unmapped_ws_symbols_logged:
                # Check if there's a similar symbol in the map (for debugging)
                similar_symbols = [
                    s
                    for s in self._subscription_map.keys()
                    if _normalize_symbol_key(s) == normalized_symbol
                ]
                logger.warning(
                    f"Unmapped websocket symbol (exact match required): {symbol} - "
                    f"normalized: {normalized_symbol} - "
                    f"similar_in_map: {similar_symbols[:3] if similar_symbols else 'none'} - "
                    f"subscription_map_size: {len(self._subscription_map)}"
                )
                self._unmapped_ws_symbols_logged.add(symbol)
            return

        received_at = datetime.now(timezone.utc).isoformat()

        # CRITICAL: Validate symbol mapping integrity
        # The WebSocket symbol MUST match the stored trading symbol to prevent LTP cross-contamination
        if subscribed_option.trading_symbol != symbol:
            logger.error(
                f"SYMBOL MAPPING INTEGRITY ERROR - ws_symbol: {symbol} != stored_trading_symbol: {subscribed_option.trading_symbol} - "
                f"strike: {subscribed_option.strike_price} type: {subscribed_option.option_type} ltp: {ltp} - "
                f"SKIPPING this tick to prevent LTP cross-contamination"
            )
            return

        # Log tick processing for debugging (first few ticks per symbol)
        tick_debug_key = (
            f"{symbol}|{subscribed_option.strike_price}|{subscribed_option.option_type}"
        )
        if tick_debug_key not in self._tick_debug_logged_symbols:
            logger.info(
                f"TICK PROCESSED - ws_symbol: {symbol} -> "
                f"trading_symbol: {subscribed_option.trading_symbol} -> "
                f"strike: {subscribed_option.strike_price} type: {subscribed_option.option_type} -> "
                f"ltp: {ltp} lookup: {lookup_method}"
            )
            self._tick_debug_logged_symbols.add(tick_debug_key)

        payload = {
            "message_type": "option_quote_update",
            "instrument_symbol": subscribed_option.instrument_symbol,
            "symbol": subscribed_option.trading_symbol,  # Always use stored symbol for Redis key
            "strike_price": subscribed_option.strike_price,
            "option_type": subscribed_option.option_type,
            "ltp": ltp,
            "avg_price": message.get("avg_trade_price") or message.get("avg_price"),
            "source_received_at": received_at,
            "last_update": received_at,
            "stale_after_seconds": LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS,
        }
        self._last_tick_at = payload["last_update"]

        # Capture subscribed_option values in lambda to avoid late-binding issues
        self._schedule_live_write(
            lambda p=payload,
            inst=subscribed_option.instrument_symbol,
            sym=subscribed_option.trading_symbol: (
                RedisLiveMarketStore.write_live_symbol(
                    instrument_symbol=inst,
                    symbol=sym,
                    payload=p,
                )
            )
        )

    async def _get_previous_close_spot(self, instrument_symbol: str) -> float | None:
        previous_day_final = (
            await RedisOptionChainSnapshotStore.get_previous_day_final_snapshot(
                instrument_symbol
            )
        )
        if not previous_day_final:
            return None
        latest = previous_day_final.get("latest") or {}
        spot_price = latest.get("spot_price")
        if spot_price is None:
            return None
        return float(spot_price)

    async def _get_market_open_data(self, instrument_symbol: str) -> dict:
        """
        Get the snapshot closest to 9:15 AM within the 9:00-9:15 window.
        Returns dict with spot_price and captured_at.
        Fallback to first snapshot of the day if none found in window.
        """
        from datetime import time as time_type

        from libs.utils.config.src.fyers import (
            MARKET_OPEN_HOUR,
            MARKET_OPEN_MINUTE,
        )

        trade_date = datetime.now(IST).date().isoformat()
        timeline = await RedisOptionChainSnapshotStore.get_timeline(
            instrument_symbol=instrument_symbol,
            trade_date=trade_date,
            limit=100,
        )
        if not timeline:
            return {}

        market_open_time = time_type(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE)
        market_open_end_time = time_type(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE + 15)
        target_time = time_type(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE + 15)  # 9:15 AM

        # Collect all snapshots within the 9:00-9:15 window
        candidates = []
        for snapshot in reversed(timeline):  # Timeline is in reverse order
            latest = snapshot.get("latest") or snapshot
            captured_at_str = latest.get("captured_at")
            if not captured_at_str:
                continue

            try:
                captured_at = datetime.fromisoformat(captured_at_str)
                captured_time = captured_at.astimezone(IST).time()

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

        # Fallback: return earliest snapshot
        if timeline:
            earliest = timeline[-1]
            latest = earliest.get("latest") or earliest
            return {
                "spot_price": latest.get("spot_price"),
                "captured_at": latest.get("captured_at"),
            }

        return {}

    async def _get_weekly_close_data(self, instrument_symbol: str) -> dict:
        """
        Get the previous weekly expiry close spot price from Redis.
        Returns dict with close_spot, expiry_date, and captured_at.
        """
        weekly_close = await RedisWeeklyCloseStore.get_weekly_close(instrument_symbol)
        if not weekly_close:
            return {}
        return {
            "close_spot": weekly_close.get("close_spot"),
            "expiry_date": weekly_close.get("expiry_date"),
            "captured_at": weekly_close.get("captured_at"),
        }

    async def _get_snapshot_subscription_rows(
        self,
        instrument_symbol: str,
    ) -> list[dict]:
        trade_date = datetime.now(IST).date().isoformat()
        latest_snapshot = await RedisOptionChainSnapshotStore.get_latest_snapshot(
            instrument_symbol=instrument_symbol,
            trade_date=trade_date,
        )
        if not latest_snapshot:
            logger.info(
                f"No snapshot found for subscription - instrument: {instrument_symbol} - "
                f"trade_date: {trade_date} - will use FYERS API fallback"
            )
            return []

        # Log snapshot details for debugging
        snapshot_strikes = latest_snapshot.get("strikes", [])
        snapshot_captured_at = (latest_snapshot.get("latest") or {}).get(
            "captured_at", "unknown"
        )
        strike_prices = sorted(
            [
                s.get("strike_price")
                for s in snapshot_strikes
                if s.get("strike_price") is not None
            ]
        )
        logger.info(
            f"Snapshot read for subscription - instrument: {instrument_symbol} - "
            f"strikes_count: {len(snapshot_strikes)} - "
            f"captured_at: {snapshot_captured_at} - "
            f"strike_range: [{strike_prices[0] if strike_prices else 'N/A'} - {strike_prices[-1] if strike_prices else 'N/A'}]"
        )

        rows: list[dict] = []
        seen_symbols: set[str] = set()
        for strike in snapshot_strikes:
            strike_price = strike.get("strike_price")
            if strike_price is None:
                continue

            call_symbol = strike.get("call_trading_symbol")
            put_symbol = strike.get("put_trading_symbol")

            # CRITICAL: Check if CALL and PUT symbols are identical (data corruption)
            if call_symbol and put_symbol and call_symbol == put_symbol:
                logger.error(
                    f"CRITICAL DATA ERROR: CALL and PUT have same trading symbol at same strike - "
                    f"instrument: {instrument_symbol} - strike: {strike_price} - "
                    f"symbol: {call_symbol} - This will cause LTP cross-contamination!"
                )
                # Skip this strike entirely to prevent corruption
                continue

            if call_symbol:
                if call_symbol in seen_symbols:
                    logger.error(
                        "Duplicate call trading symbol found in latest snapshot - "
                        f"instrument_symbol: {instrument_symbol} - "
                        f"strike_price: {strike_price} - "
                        f"symbol: {call_symbol}"
                    )
                else:
                    seen_symbols.add(call_symbol)
                    rows.append(
                        {
                            "trading_symbol": call_symbol,
                            "strike_price": strike_price,
                            "option_type": "CE",
                        }
                    )

            if put_symbol:
                if put_symbol in seen_symbols:
                    logger.error(
                        "Duplicate put trading symbol found in latest snapshot - "
                        f"instrument_symbol: {instrument_symbol} - "
                        f"strike_price: {strike_price} - "
                        f"symbol: {put_symbol}"
                    )
                else:
                    seen_symbols.add(put_symbol)
                    rows.append(
                        {
                            "trading_symbol": put_symbol,
                            "strike_price": strike_price,
                            "option_type": "PE",
                        }
                    )
        return rows

    def _schedule_live_write(self, coroutine_factory) -> None:
        if not self._loop:
            return

        now = monotonic()
        if now < self._redis_retry_after:
            return

        coroutine = coroutine_factory()
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        future.add_done_callback(self._handle_live_write_result)

    def _handle_live_write_result(self, future) -> None:
        try:
            future.result()
        except Exception as error:
            self._handle_redis_write_error(error)
            return

        if self._redis_degraded:
            logger.info("Redis live write path recovered")
        self._redis_degraded = False
        self._redis_failure_count = 0
        self._redis_retry_after = 0.0

    def _handle_redis_write_error(self, error: Exception) -> None:
        self._redis_failure_count += 1
        backoff_seconds = min(30, 2 ** min(self._redis_failure_count, 4))
        self._redis_retry_after = monotonic() + backoff_seconds
        self._redis_degraded = True
        _log_throttled(
            level="error",
            message=(
                "Failed to write live symbol update - "
                f"error: {str(error)} - retry_backoff_seconds: {backoff_seconds}"
            ),
            last_logged_at_attr="_last_redis_error_log_at",
            suppressed_count_attr="_suppressed_redis_error_count",
            owner=self,
            window_seconds=10,
        )


def _log_throttled(
    *,
    level: str,
    message: str,
    last_logged_at_attr: str,
    suppressed_count_attr: str,
    owner,
    window_seconds: int,
) -> None:
    now = monotonic()
    last_logged_at = getattr(owner, last_logged_at_attr)
    suppressed_count = getattr(owner, suppressed_count_attr)

    if now - last_logged_at < window_seconds:
        setattr(owner, suppressed_count_attr, suppressed_count + 1)
        return

    suffix = ""
    if suppressed_count:
        suffix = f" - suppressed_duplicates: {suppressed_count}"
    getattr(logger, level)(f"{message}{suffix}")
    setattr(owner, last_logged_at_attr, now)
    setattr(owner, suppressed_count_attr, 0)


def _is_fyers_auth_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "fyers token for today is missing",
            "access token",
            "invalid token",
            "token expired",
            "unauthorized",
            "authentication",
        )
    )


def _is_missing_daily_token_error(error: Exception) -> bool:
    return "fyers token for today is missing" in str(error).lower()


def _build_underlying_payload(
    *,
    instrument_symbol: str,
    symbol: str,
    spot_price,
    prev_close_spot: float | None,
    market_open_spot: float | None = None,
    market_open_captured_at: str | None = None,
    weekly_close_spot: float | None = None,
    weekly_close_expiry_date: str | None = None,
    weekly_close_captured_at: str | None = None,
    stale_after_seconds: int,
) -> dict:
    normalized_spot_price = _as_float(spot_price)
    received_at = datetime.now(timezone.utc).isoformat()
    change_from_prev_close = None
    change_pct_from_prev_close = None
    if normalized_spot_price is not None and prev_close_spot is not None:
        change_from_prev_close = normalized_spot_price - prev_close_spot
        if prev_close_spot != 0:
            change_pct_from_prev_close = (
                change_from_prev_close / prev_close_spot
            ) * 100

    # Calculate today's movement from open
    today_from_open_pct = None
    if (
        normalized_spot_price is not None
        and market_open_spot is not None
        and market_open_spot != 0
    ):
        today_from_open_pct = (
            (normalized_spot_price - market_open_spot) / market_open_spot
        ) * 100

    # Calculate weekly movement from previous close
    weekly_from_close_pct = None
    if (
        normalized_spot_price is not None
        and weekly_close_spot is not None
        and weekly_close_spot != 0
    ):
        weekly_from_close_pct = (
            (normalized_spot_price - weekly_close_spot) / weekly_close_spot
        ) * 100

    return {
        "message_type": "underlying_spot_update",
        "instrument_symbol": instrument_symbol,
        "symbol": symbol,
        "spot_price": normalized_spot_price,
        "prev_close_spot": prev_close_spot,
        "change_from_prev_close": change_from_prev_close,
        "change_pct_from_prev_close": change_pct_from_prev_close,
        "source_received_at": received_at,
        "last_update": received_at,
        "stale_after_seconds": stale_after_seconds,
        # Movement data
        "movements": {
            "today_from_open": {
                "open_spot": market_open_spot,
                "movement_pct": round(today_from_open_pct, 2)
                if today_from_open_pct is not None
                else None,
                "captured_at_open": market_open_captured_at,
            }
            if market_open_spot is not None
            else None,
            "weekly_from_close": {
                "prev_week_close": weekly_close_spot,
                "movement_pct": round(weekly_from_close_pct, 2)
                if weekly_from_close_pct is not None
                else None,
                "expiry_date": weekly_close_expiry_date,
                "captured_at_close": weekly_close_captured_at,
            }
            if weekly_close_spot is not None
            else None,
        },
    }


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_symbol_key(symbol: str) -> str:
    return str(symbol).strip().upper()


def _normalize_strike_key(value) -> str:
    decimal_value = Decimal(str(value))
    normalized = format(decimal_value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _dedupe_rows_by_strike_and_option_type(rows: list[dict]) -> list[dict]:
    deduped_rows: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    for row in rows:
        option_type = str(row.get("option_type", "")).upper()
        strike_price = row.get("strike_price")
        if not option_type or strike_price is None:
            continue
        row_key = (_normalize_strike_key(strike_price), option_type)
        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)
        deduped_rows.append(row)
    return deduped_rows
