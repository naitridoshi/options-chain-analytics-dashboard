import asyncio
import signal
import sys

from apps.ingestion.platform.modules.snapshot_merge.src.scheduler import (
    get_snapshot_merge_scheduler,
)
from apps.ingestion.platform.modules.symbol_refresh.src.scheduler import (
    SymbolRefreshManager,
    get_symbol_refresh_scheduler,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.events.src import (
    SymbolListRefreshedEvent,
    TokenRefreshedEvent,
)
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.option_symbols.src import build_symbol_to_strike_mapping
from libs.utils.state.src import AppState

log = CustomLogger("IngestionService")
logger, listener = log.get_logger()
listener.start()


class IngestionService:
    """Main ingestion service orchestrating WebSocket and symbol refresh."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.market_state = app_state.market_state
        self.market_data_manager = app_state.market_data_manager
        self.event_dispatcher = app_state.event_dispatcher
        self.symbol_refresh_manager = SymbolRefreshManager()
        self._running = False

        # Schedulers will be created during initialize
        self._symbol_refresh_scheduler = None
        self._snapshot_merge_scheduler = None

    async def initialize(self) -> None:
        """Initialize ingestion service."""
        try:
            logger.info("Initializing ingestion service...")

            # Setup event handlers
            self.event_dispatcher.subscribe("TOKEN_REFRESHED", self._on_token_refreshed)
            self.event_dispatcher.subscribe(
                "SYMBOL_LIST_REFRESHED", self._on_symbol_list_refreshed
            )

            # Initial symbol refresh
            logger.info("Performing initial symbol refresh...")
            refresh_results = (
                await self.symbol_refresh_manager.refresh_symbols_for_all_instruments()
            )

            # Get all symbols
            all_symbols = []
            for result in refresh_results.values():
                if result and result.get("symbols"):
                    all_symbols.extend(result["symbols"])

            if not all_symbols:
                logger.warning(
                    "No symbols available for WebSocket subscription after refresh"
                )
                return

            # Update market state with symbol mappings
            if refresh_results:
                first_result = next((r for r in refresh_results.values() if r), None)
                if first_result:
                    symbol_to_strike, _ = build_symbol_to_strike_mapping(
                        first_result.get("symbol_mapping", {})
                    )
                    self.market_state.update_symbol_mapping(
                        symbol_to_strike, first_result.get("expiry_date")
                    )

            # Start market data collection
            access_token = await FyersClientService.get_valid_access_token()
            await self.market_data_manager.start(access_token, all_symbols)

            # Create and start schedulers with injected state
            self._symbol_refresh_scheduler = get_symbol_refresh_scheduler(
                self.app_state
            )
            self._snapshot_merge_scheduler = get_snapshot_merge_scheduler(
                self.app_state
            )

            await self._symbol_refresh_scheduler.start()
            await self._snapshot_merge_scheduler.start()

            self._running = True
            logger.info("Ingestion service initialized successfully")

        except Exception as error:
            logger.error(
                f"Failed to initialize ingestion service - error: {str(error)}"
            )
            raise

    async def shutdown(self) -> None:
        """Shutdown ingestion service."""
        try:
            logger.info("Shutting down ingestion service...")
            self._running = False

            if self.market_data_manager:
                await self.market_data_manager.stop()

            if self._symbol_refresh_scheduler:
                await self._symbol_refresh_scheduler.stop()

            if self._snapshot_merge_scheduler:
                await self._snapshot_merge_scheduler.stop()

            logger.info("Ingestion service shutdown complete")
        except Exception as error:
            logger.error(f"Error during shutdown - error: {str(error)}")

    async def _on_token_refreshed(self, event: TokenRefreshedEvent) -> None:
        """Handle token refresh event.

        Args:
            event: TokenRefreshedEvent
        """
        try:
            logger.info("Token refreshed event received, restarting market data...")
            access_token = event.data.get("access_token")

            # Get current symbols
            symbols = self.market_state.get_all_symbols()
            if not symbols:
                logger.warning("No symbols available for restart after token refresh")
                return

            # Restart with new token
            await self.market_data_manager.restart(access_token, list(symbols.keys()))

        except Exception as error:
            logger.error(f"Error handling token refresh - error: {str(error)}")

    async def _on_symbol_list_refreshed(self, event: SymbolListRefreshedEvent) -> None:
        """Handle symbol list refresh event (expiry change).

        Args:
            event: SymbolListRefreshedEvent
        """
        try:
            logger.info("Symbol list refreshed event received...")
            symbols = event.data.get("symbols", [])

            if not symbols:
                logger.warning("No symbols in refresh event")
                return

            # Update subscribed symbols
            await self.market_data_manager.update_symbols(symbols)

        except Exception as error:
            logger.error(f"Error handling symbol refresh - error: {str(error)}")


async def run_ingestion() -> None:
    """Run ingestion service."""
    from libs.utils.state.src.factory import create_app_state_with_market_data

    # Create app state with market data manager
    app_state = await create_app_state_with_market_data()

    # Create ingestion service with injected state
    service = IngestionService(app_state)

    stop_event = asyncio.Event()

    def _handle_stop(*_) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _handle_stop)
        loop.add_signal_handler(signal.SIGTERM, _handle_stop)

    try:
        await service.initialize()
        logger.info("Ingestion service running, waiting for shutdown signal...")
        await stop_event.wait()
    finally:
        await service.shutdown()
