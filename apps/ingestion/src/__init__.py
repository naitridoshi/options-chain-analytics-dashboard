import asyncio
import signal
import sys

from apps.ingestion.platform.modules.option_chain_market_data.src import (
    get_market_data_manager,
)
from apps.ingestion.platform.modules.snapshot_merge.src import (
    get_snapshot_merge_scheduler,
)
from apps.ingestion.platform.modules.symbol_refresh.src import (
    SymbolRefreshManager,
    symbol_refresh_scheduler,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.events.src import (
    SymbolListRefreshedEvent,
    TokenRefreshedEvent,
    get_event_dispatcher,
)
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.market_state.src import get_market_state_manager

log = CustomLogger("IngestionService")
logger, listener = log.get_logger()
listener.start()


class IngestionService:
    """Main ingestion service orchestrating WebSocket and symbol refresh."""

    def __init__(self):
        self.market_data_manager = get_market_data_manager()
        self.symbol_refresh_manager = SymbolRefreshManager()
        self.symbol_refresh_scheduler = symbol_refresh_scheduler
        self.snapshot_merge_scheduler = get_snapshot_merge_scheduler()
        self.event_dispatcher = get_event_dispatcher()
        self.market_state = get_market_state_manager()
        self._running = False

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
                    # For single-instrument setup, use the first result's mapping
                    symbol_to_strike = {}
                    mapping = first_result.get("symbol_mapping", {})
                    for strike, symbols_dict in mapping.items():
                        for symbol in symbols_dict.values():
                            symbol_to_strike[symbol] = strike
                    self.market_state.update_symbol_mapping(
                        symbol_to_strike, first_result.get("expiry_date")
                    )

            # Start market data collection
            access_token = await FyersClientService.get_valid_access_token()
            await self.market_data_manager.start(access_token, all_symbols)

            # Start symbol refresh scheduler
            await self.symbol_refresh_scheduler.start()

            # Start snapshot merge scheduler
            await self.snapshot_merge_scheduler.start()

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
            await self.market_data_manager.stop()
            await self.symbol_refresh_scheduler.stop()
            await self.snapshot_merge_scheduler.stop()
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


_ingestion_service: IngestionService | None = None


def get_ingestion_service() -> IngestionService:
    """Get or create ingestion service instance."""
    global _ingestion_service
    if _ingestion_service is None:
        _ingestion_service = IngestionService()
    return _ingestion_service


async def run_ingestion() -> None:
    """Run ingestion service."""
    service = get_ingestion_service()
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
