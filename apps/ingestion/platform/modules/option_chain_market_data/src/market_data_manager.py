import asyncio

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.events.src import get_event_dispatcher
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.market_state.src import get_market_state_manager
from libs.utils.common.websocket.src import (
    RetryConfig,
    WebSocketReconnectManager,
)

log = CustomLogger("OptionChainMarketDataManager")
logger, listener = log.get_logger()
listener.start()


class OptionChainMarketDataManager:
    """Manages WebSocket connection for option chain market data."""

    def __init__(self):
        self._ws_client = None
        self._task: asyncio.Task | None = None
        self._is_running = False
        self._reconnect_manager = WebSocketReconnectManager(
            config=RetryConfig(
                initial_delay=1.0,
                max_delay=60.0,
                backoff_factor=2.0,
                jitter=True,
            )
        )
        self._current_symbols: list[str] = []
        self._event_dispatcher = get_event_dispatcher()
        self._market_state = get_market_state_manager()

    async def start(
        self,
        access_token: str,
        symbols: list[str],
    ) -> None:
        """Start WebSocket market data stream.

        Args:
            access_token: Valid FYERS access token
            symbols: List of symbols to subscribe
        """
        if self._is_running:
            logger.warning("Market data manager already running")
            return

        try:
            self._current_symbols = symbols
            self._is_running = True

            # Create WebSocket client with callbacks
            self._ws_client = FyersClientService.create_websocket_client(access_token)

            # Set up callbacks
            self._setup_callbacks()

            # Subscribe to symbols
            FyersClientService.subscribe_symbols(
                self._ws_client, self._current_symbols, data_type="symbolData"
            )

            # Start listening in background task
            self._task = asyncio.create_task(self._run_websocket_loop())

            logger.info(
                f"Market data manager started - symbols: {len(self._current_symbols)}"
            )

        except Exception as error:
            self._is_running = False
            logger.error(f"Failed to start market data manager - error: {str(error)}")
            raise

    async def stop(self) -> None:
        """Stop WebSocket market data stream."""
        if not self._is_running:
            return

        try:
            self._is_running = False

            if self._ws_client:
                if self._current_symbols:
                    FyersClientService.unsubscribe_symbols(
                        self._ws_client, self._current_symbols
                    )
                self._ws_client.close()
                self._ws_client = None

            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None

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
        if not self._ws_client:
            logger.warning("WebSocket client not initialized")
            return

        try:
            # Unsubscribe old symbols
            if self._current_symbols:
                FyersClientService.unsubscribe_symbols(
                    self._ws_client, self._current_symbols
                )
                logger.info(
                    f"Unsubscribed old symbols - count: {len(self._current_symbols)}"
                )

            # Subscribe new symbols
            self._current_symbols = symbols
            FyersClientService.subscribe_symbols(
                self._ws_client, self._current_symbols, data_type="symbolData"
            )
            logger.info(f"Subscribed new symbols - count: {len(self._current_symbols)}")

        except Exception as error:
            logger.error(
                f"Failed to update symbols - error: {str(error)}, count: {len(symbols)}"
            )

    def _setup_callbacks(self) -> None:
        """Setup WebSocket event callbacks."""
        self._ws_client.on_message(self._on_message)
        self._ws_client.on_connect(self._on_connect)
        self._ws_client.on_disconnect(self._on_disconnect)
        self._ws_client.on_error(self._on_error)

    async def _run_websocket_loop(self) -> None:
        """Run WebSocket event loop with reconnection logic."""
        while self._is_running:
            try:
                self._ws_client.connect()
                self._reconnect_manager.on_connect_success()
                logger.info("WebSocket connected")

                # Wait while connected
                while self._ws_client.is_connected and self._is_running:
                    await asyncio.sleep(0.1)

            except Exception as error:
                logger.error(f"WebSocket error in loop - error: {str(error)}")
                self._reconnect_manager.on_connect_failure()

                if not self._is_running:
                    break

                # Wait before retry
                await self._reconnect_manager.wait_before_retry()

    def _on_message(self, message: dict) -> None:
        """Handle WebSocket message (market tick).

        Args:
            message: Tick data from FYERS WebSocket
        """
        try:
            if not isinstance(message, dict):
                return

            # Extract fields from FYERS format
            symbol = message.get("symbol")
            if not symbol:
                return

            # Update market state with tick data
            self._market_state.update_tick(
                symbol=symbol,
                ltp=message.get("ltp"),
                avg_price=message.get("av") or message.get("avg_price"),
                volume=message.get("v") or message.get("volume"),
                oi=message.get("oi") or message.get("open_interest"),
                bid=message.get("bid"),
                ask=message.get("ask"),
            )

        except Exception as error:
            logger.debug(f"Error processing message - error: {str(error)}")

    def _on_connect(self) -> None:
        """Handle WebSocket connect event."""
        logger.info("WebSocket connected")

    def _on_disconnect(self) -> None:
        """Handle WebSocket disconnect event."""
        logger.warning("WebSocket disconnected")

    def _on_error(self, error: Exception) -> None:
        """Handle WebSocket error event.

        Args:
            error: Exception from WebSocket
        """
        logger.error(f"WebSocket error - error: {str(error)}")


# Global instance
_market_data_manager: OptionChainMarketDataManager | None = None


def get_market_data_manager() -> OptionChainMarketDataManager:
    """Get or create the global market data manager.

    Returns:
        OptionChainMarketDataManager: The global instance
    """
    global _market_data_manager
    if _market_data_manager is None:
        _market_data_manager = OptionChainMarketDataManager()
    return _market_data_manager
