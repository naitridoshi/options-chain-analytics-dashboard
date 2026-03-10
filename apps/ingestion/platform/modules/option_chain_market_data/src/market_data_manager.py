import asyncio
import threading

from fyers_apiv3.FyersWebsocket import data_ws

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.events.src import EventDispatcher
from libs.utils.common.market_state.src import MarketStateManager
from libs.utils.common.websocket.src import (
    RetryConfig,
    WebSocketReconnectManager,
)
from libs.utils.config.src.fyers import FYERS_APP_ID, FYERS_LOG_PATH

log = CustomLogger("OptionChainMarketDataManager")
logger, listener = log.get_logger()
listener.start()


class OptionChainMarketDataManager:
    """Manages WebSocket connection for option chain market data.

    Uses FYERS data_ws.FyersDataSocket for real-time market data.
    """

    def __init__(
        self,
        market_state: MarketStateManager,
        event_dispatcher: EventDispatcher | None = None,
    ):
        self._market_state = market_state
        self._event_dispatcher = event_dispatcher
        self._ws_client: data_ws.FyersDataSocket | None = None
        self._is_running = False
        self._is_connected = False
        self._reconnect_manager = WebSocketReconnectManager(
            config=RetryConfig(
                initial_delay=1.0,
                max_delay=60.0,
                backoff_factor=2.0,
                jitter=True,
            )
        )
        self._current_symbols: list[str] = []
        self._access_token: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None

    async def start(
        self,
        access_token: str,
        symbols: list[str],
    ) -> None:
        """Start WebSocket market data stream.

        Args:
            access_token: Valid FYERS access token (just the token, not appid:token)
            symbols: List of symbols to subscribe
        """
        if self._is_running:
            logger.warning("Market data manager already running")
            return

        try:
            self._access_token = access_token
            self._current_symbols = symbols
            self._loop = asyncio.get_running_loop()
            self._is_running = True

            # Create and start WebSocket in a separate thread
            self._create_and_start_websocket()

            logger.info(
                f"Market data manager started - symbols: {len(self._current_symbols)}"
            )

        except Exception as error:
            self._is_running = False
            logger.error(f"Failed to start market data manager - error: {str(error)}")
            raise

    def _create_and_start_websocket(self) -> None:
        """Create WebSocket client and start in background thread."""
        # The access token needs to be in format "appid:accesstoken"
        full_token = f"{FYERS_APP_ID}:{self._access_token}"

        self._ws_client = data_ws.FyersDataSocket(
            access_token=full_token,
            log_path=FYERS_LOG_PATH,
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=self._on_connect,
            on_close=self._on_close,
            on_error=self._on_error,
            on_message=self._on_message,
        )

        # Start WebSocket in a separate thread to not block async loop
        self._ws_thread = threading.Thread(target=self._ws_client.connect, daemon=True)
        self._ws_thread.start()

    async def stop(self) -> None:
        """Stop WebSocket market data stream."""
        if not self._is_running:
            return

        try:
            self._is_running = False
            self._is_connected = False

            if self._ws_client:
                # FYERS WebSocket doesn't have explicit close, but we can stop the thread
                self._ws_client.keep_running = False
                self._ws_client = None

            self._reconnect_manager.reset()
            logger.info("Market data manager stopped")

        except Exception as error:
            logger.error(f"Error stopping market data manager - error: {str(error)}")

    async def restart(self, access_token: str, symbols: list[str]) -> None:
        """Restart WebSocket with new symbols or token.

        Args:
            access_token: New FYERS access token
            symbols: List of symbols to subscribe
        """
        try:
            logger.info(f"Restarting market data manager - symbols: {len(symbols)}")
            await self.stop()
            await asyncio.sleep(0.5)  # Small delay before restart
            await self.start(access_token, symbols)
        except Exception as error:
            logger.error(f"Failed to restart market data manager - error: {str(error)}")
            raise

    async def update_symbols(self, symbols: list[str]) -> None:
        """Update subscribed symbols (for expiry refresh).

        Args:
            symbols: New list of symbols to subscribe
        """
        if not self._ws_client or not self._is_connected:
            logger.warning(
                "WebSocket client not connected, storing symbols for reconnect"
            )
            self._current_symbols = symbols
            return

        try:
            # Unsubscribe old symbols
            if self._current_symbols:
                self._ws_client.unsubscribe(symbols=self._current_symbols)
                logger.info(
                    f"Unsubscribed old symbols - count: {len(self._current_symbols)}"
                )

            # Subscribe new symbols
            self._current_symbols = symbols
            self._ws_client.subscribe(
                symbols=self._current_symbols, data_type="SymbolUpdate"
            )
            logger.info(f"Subscribed new symbols - count: {len(self._current_symbols)}")

        except Exception as error:
            logger.error(
                f"Failed to update symbols - error: {str(error)}, count: {len(symbols)}"
            )

    def _on_connect(self) -> None:
        """Handle WebSocket connect event. Called from WebSocket thread."""
        self._is_connected = True
        self._reconnect_manager.on_connect_success()
        logger.info("WebSocket connected")

        # Subscribe to symbols upon connection
        if self._current_symbols:
            try:
                self._ws_client.subscribe(
                    symbols=self._current_symbols, data_type="SymbolUpdate"
                )
                logger.info(
                    f"Subscribed to symbols on connect - count: {len(self._current_symbols)}"
                )
            except Exception as error:
                logger.error(f"Failed to subscribe on connect - error: {str(error)}")

    def _on_close(self) -> None:
        """Handle WebSocket close event. Called from WebSocket thread."""
        self._is_connected = False
        logger.warning("WebSocket disconnected")

    def _on_error(self, error: Exception) -> None:
        """Handle WebSocket error event. Called from WebSocket thread.

        Args:
            error: Exception from WebSocket
        """
        self._is_connected = False
        self._reconnect_manager.on_connect_failure()
        logger.error(f"WebSocket error - error: {str(error)}")

    def _on_message(self, message: dict) -> None:
        """Handle WebSocket message (market tick). Called from WebSocket thread.

        Args:
            message: Tick data from FYERS WebSocket
        """
        try:
            if not isinstance(message, dict):
                return

            # Extract fields from FYERS SymbolUpdate format
            # Response format: {"symbol": "...", "ltp": ..., "vol_traded_today": ..., ...}
            symbol = message.get("symbol")
            if not symbol:
                return

            # Update market state with tick data
            # FYERS SymbolUpdate fields mapping:
            # - ltp: Last traded price
            # - vol_traded_today: Volume
            # - avg_trade_price (or similar): Average price
            # - bid_price/bid_size: Bid data
            # - ask_price/ask_size: Ask data
            ltp = message.get("ltp")
            volume = message.get("vol_traded_today") or message.get("volume")
            avg_price = message.get("avg_trade_price") or message.get("avg_price")
            bid = message.get("bid_price") or message.get("bid")
            ask = message.get("ask_price") or message.get("ask")

            # For options, OI might be available
            oi = message.get("oi") or message.get("open_interest")

            self._market_state.update_tick(
                symbol=symbol,
                ltp=ltp,
                avg_price=avg_price,
                volume=volume,
                oi=oi,
                bid=bid,
                ask=ask,
            )

        except Exception as error:
            logger.debug(f"Error processing message - error: {str(error)}")

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._is_connected

    @property
    def is_running(self) -> bool:
        """Check if manager is running."""
        return self._is_running
