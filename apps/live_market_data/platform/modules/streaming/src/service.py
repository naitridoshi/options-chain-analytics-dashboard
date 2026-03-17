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
        normalized_subscription_map: dict[str, SubscribedOption | None] = {}
        underlying_map: dict[str, UnderlyingSubscription] = {}
        all_symbols: list[str] = []

        for instrument in instruments:
            if not instrument.fyers_symbol:
                continue
            prev_close_spot = await self._get_previous_close_spot(instrument.symbol)
            underlying_map[_normalize_symbol_key(instrument.fyers_symbol)] = (
                UnderlyingSubscription(
                    instrument_symbol=instrument.symbol,
                    symbol=instrument.fyers_symbol,
                    prev_close_spot=prev_close_spot,
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
                if trading_symbol in subscription_map:
                    logger.error(
                        "Duplicate websocket subscription symbol collision - "
                        f"symbol: {trading_symbol} - instrument: {instrument.symbol}"
                    )
                subscribed_option = SubscribedOption(
                    instrument_symbol=instrument.symbol,
                    strike_price=strike_key,
                    option_type=option_type,
                    trading_symbol=trading_symbol,
                )
                subscription_map[trading_symbol] = subscribed_option
                normalized_key = _normalize_symbol_key(trading_symbol)
                existing_normalized = normalized_subscription_map.get(normalized_key)
                if (
                    existing_normalized
                    and existing_normalized.trading_symbol != trading_symbol
                ):
                    logger.error(
                        "Normalized websocket symbol collision detected - "
                        f"normalized_symbol: {normalized_key} - "
                        f"existing_symbol: {existing_normalized.trading_symbol} - "
                        f"incoming_symbol: {trading_symbol}"
                    )
                    normalized_subscription_map[normalized_key] = None
                elif normalized_key not in normalized_subscription_map:
                    normalized_subscription_map[normalized_key] = subscribed_option
                all_symbols.append(trading_symbol)

        all_symbols = sorted(set(all_symbols))
        symbols_changed = all_symbols != self._current_symbols
        self._subscription_map = subscription_map
        self._normalized_subscription_map = normalized_subscription_map
        self._underlying_map = underlying_map
        self._unmapped_ws_symbols_logged.clear()

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
        if not symbol or not self._loop:
            return
        normalized_symbol = _normalize_symbol_key(symbol)

        underlying_subscription = self._underlying_map.get(normalized_symbol)
        if underlying_subscription:
            payload = _build_underlying_payload(
                instrument_symbol=underlying_subscription.instrument_symbol,
                symbol=underlying_subscription.symbol,
                spot_price=message.get("ltp"),
                prev_close_spot=underlying_subscription.prev_close_spot,
                stale_after_seconds=LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS,
            )
            self._last_tick_at = payload["last_update"]
            self._schedule_live_write(
                lambda: RedisLiveMarketStore.write_live_underlying(
                    instrument_symbol=underlying_subscription.instrument_symbol,
                    payload=payload,
                )
            )
            return

        subscribed_option = self._subscription_map.get(symbol)
        if not subscribed_option:
            subscribed_option = self._normalized_subscription_map.get(normalized_symbol)
        if not subscribed_option:
            if symbol not in self._unmapped_ws_symbols_logged:
                logger.warning(f"Unmapped websocket symbol: {symbol}")
                self._unmapped_ws_symbols_logged.add(symbol)
            return

        received_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "message_type": "option_quote_update",
            "instrument_symbol": subscribed_option.instrument_symbol,
            "symbol": subscribed_option.trading_symbol,
            "strike_price": subscribed_option.strike_price,
            "option_type": subscribed_option.option_type,
            "ltp": message.get("ltp"),
            "avg_price": message.get("avg_trade_price") or message.get("avg_price"),
            "source_received_at": received_at,
            "last_update": received_at,
            "stale_after_seconds": LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS,
        }
        self._last_tick_at = payload["last_update"]

        self._schedule_live_write(
            lambda: RedisLiveMarketStore.write_live_symbol(
                instrument_symbol=subscribed_option.instrument_symbol,
                symbol=subscribed_option.trading_symbol,
                payload=payload,
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
            return []

        rows: list[dict] = []
        for strike in latest_snapshot.get("strikes", []):
            strike_price = strike.get("strike_price")
            if strike_price is None:
                continue

            call_symbol = strike.get("call_trading_symbol")
            if call_symbol:
                rows.append(
                    {
                        "trading_symbol": call_symbol,
                        "strike_price": strike_price,
                        "option_type": "CE",
                    }
                )

            put_symbol = strike.get("put_trading_symbol")
            if put_symbol:
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
