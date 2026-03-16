from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from fyers_apiv3.FyersWebsocket import data_ws

from libs.platform.modules.option_chain_snapshot.src import (
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
from libs.utils.db.redis.src import RedisLiveMarketStore

log = CustomLogger("LiveMarketStreamingService")
logger, listener = log.get_logger()
listener.start()


@dataclass
class SubscribedOption:
    instrument_symbol: str
    strike_price: str
    option_type: str
    trading_symbol: str


class LiveMarketStreamingService:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._ws_client: data_ws.FyersDataSocket | None = None
        self._ws_thread: threading.Thread | None = None
        self._subscription_map: dict[str, SubscribedOption] = {}
        self._current_symbols: list[str] = []
        self._is_connected = False
        self._last_tick_at: str | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._subscription_refresh_loop())
        try:
            await self._refresh_subscriptions(force_restart=True)
        except Exception as error:
            await self._handle_refresh_error(error, during_startup=True)
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
                await asyncio.sleep(LIVE_DATA_SYMBOL_REFRESH_INTERVAL_SECONDS)
                await self._refresh_subscriptions(force_restart=False)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._handle_refresh_error(error, during_startup=False)
                await asyncio.sleep(5)

    async def _refresh_subscriptions(self, *, force_restart: bool) -> None:
        instruments = InstrumentCatalogService.get_active_instruments()
        subscription_map: dict[str, SubscribedOption] = {}
        all_symbols: list[str] = []

        for instrument in instruments:
            if not instrument.fyers_symbol:
                continue
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
            for row in rows:
                trading_symbol = row.get("trading_symbol")
                strike_price = row.get("strike_price")
                option_type = row.get("option_type")
                if not trading_symbol or strike_price is None or not option_type:
                    continue
                strike_key = str(int(float(strike_price)))
                subscription_map[trading_symbol] = SubscribedOption(
                    instrument_symbol=instrument.symbol,
                    strike_price=strike_key,
                    option_type=option_type,
                    trading_symbol=trading_symbol,
                )
                all_symbols.append(trading_symbol)

        all_symbols = sorted(set(all_symbols))
        symbols_changed = all_symbols != self._current_symbols
        self._subscription_map = subscription_map

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
            reconnect=True,
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

    def _pause_socket(self) -> None:
        if self._ws_client:
            self._ws_client.keep_running = False
            self._ws_client = None
        self._is_connected = False

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
        self._is_connected = False
        logger.warning("Live websocket disconnected")

    def _on_error(self, error: Exception) -> None:
        self._is_connected = False
        logger.error(f"Live websocket error - error: {str(error)}")

    def _on_message(self, message: dict) -> None:
        if not isinstance(message, dict):
            return

        symbol = message.get("symbol")
        if not symbol or not self._loop:
            return

        subscribed_option = self._subscription_map.get(symbol)
        if not subscribed_option:
            return

        payload = {
            "instrument_symbol": subscribed_option.instrument_symbol,
            "symbol": subscribed_option.trading_symbol,
            "strike_price": subscribed_option.strike_price,
            "option_type": subscribed_option.option_type,
            "ltp": message.get("ltp"),
            "avg_price": message.get("avg_trade_price") or message.get("avg_price"),
            "last_update": datetime.now(timezone.utc).isoformat(),
            "stale_after_seconds": LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS,
        }
        self._last_tick_at = payload["last_update"]

        future = asyncio.run_coroutine_threadsafe(
            RedisLiveMarketStore.write_live_symbol(
                instrument_symbol=subscribed_option.instrument_symbol,
                symbol=subscribed_option.trading_symbol,
                payload=payload,
            ),
            self._loop,
        )
        future.add_done_callback(_log_future_error)


def _log_future_error(future) -> None:
    try:
        future.result()
    except Exception as error:
        logger.error(f"Failed to write live symbol update - error: {str(error)}")


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
