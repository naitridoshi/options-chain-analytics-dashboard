from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.ingestion.platform.modules.option_chain_market_data.src import (
    OptionChainMarketDataManager,
)
from apps.ingestion.platform.modules.snapshot_merge.src.scheduler import (
    get_snapshot_merge_scheduler,
)
from apps.ingestion.platform.modules.symbol_refresh.src.scheduler import (
    SymbolRefreshManager,
    get_symbol_refresh_scheduler,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.events.src import EventDispatcher
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.market_state.src import MarketStateManager
from libs.utils.common.option_symbols.src import build_symbol_to_strike_mapping
from libs.utils.common.websocket.src.broadcaster import get_broadcaster
from libs.utils.config.src.fyers import (
    LIVE_DATA_WEBSOCKET_BROADCAST_INTERVAL_MS,
)
from libs.utils.db.postgres.operations.src import (
    InstrumentOperations,
    ScriptOperations,
)
from libs.utils.state.src import AppState

log = CustomLogger("FastAPI Lifespan")
logger, listener = log.get_logger()
listener.start()


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """FastAPI application lifespan context manager.

    Handles:
    - Database seeding for instruments and scripts
    - Application state initialization
    - Market data WebSocket connection
    - Scheduler startup (symbol refresh, snapshot merge)
    - WebSocket broadcaster for live data
    - Cleanup on shutdown
    """
    # Initialize app state
    app.state.app_state = await _initialize_app_state(app)

    yield

    # Cleanup
    await _shutdown_app_state(app)


async def _initialize_app_state(app: FastAPI) -> AppState:
    """Initialize all application components."""
    from libs.utils.common.constants.src.seeder import (
        INSTRUMENTS_FILE_PATH,
        SCRIPTS_FILE_PATH,
    )

    # Seed database with instruments
    seed_result = await InstrumentOperations.seed_missing_instruments_from_file(
        INSTRUMENTS_FILE_PATH
    )
    logger.info(
        f"Instrument seed check complete "
        f"inserted_count: {len(seed_result['inserted_symbols'])} "
        f"skipped_count: {len(seed_result['skipped_symbols'])}"
    )

    # Seed database with scripts
    scripts_seed_result = await ScriptOperations.seed_missing_scripts_from_file(
        SCRIPTS_FILE_PATH
    )
    logger.info(
        f"Script seed check complete "
        f"inserted_count: {len(scripts_seed_result['inserted_symbols'])} "
        f"skipped_count: {len(scripts_seed_result['skipped_symbols'])}"
    )

    # Create app state
    state = AppState()

    # Store app reference for token watcher
    state._app = app

    # Initialize core services
    state.event_dispatcher = EventDispatcher()
    state.market_state = MarketStateManager()

    # Initialize market data manager
    state.market_data_manager = OptionChainMarketDataManager(
        market_state=state.market_state,
        event_dispatcher=state.event_dispatcher,
    )

    # Initialize WebSocket broadcaster for live data
    state.broadcaster = get_broadcaster()
    await state.broadcaster.start(
        market_state=state.market_state,
        broadcast_interval_ms=LIVE_DATA_WEBSOCKET_BROADCAST_INTERVAL_MS,
    )

    logger.info("Application state initialized")

    # Try to start ingestion immediately if token available
    try:
        await start_ingestion(app, state)
        state.mark_ingestion_started()
        logger.info("Ingestion started successfully")
    except Exception as error:
        logger.warning(f"Ingestion not started - error: {str(error)}")
        logger.info(
            "Dashboard will show historical data only. "
            "Token watcher will auto-start ingestion when token is available."
        )

    # Start token watcher (for polling and reconnection)
    from apps.fastapi.src.token_watcher import TokenWatcher

    state._token_watcher = TokenWatcher(state)
    await state._token_watcher.start()

    # Subscribe to WebSocket auth errors for reconnection
    async def handle_ws_auth_error(event):
        logger.warning("WebSocket auth error detected, marking ingestion for restart")
        state.mark_ingestion_stopped()
        # Token watcher will detect and restart ingestion

    state.event_dispatcher.subscribe("WEBSOCKET_AUTH_ERROR", handle_ws_auth_error)

    state.mark_initialized()
    return state


async def start_ingestion(app: FastAPI, state: AppState) -> None:
    """Start ingestion services (WebSocket + schedulers).

    Can be called during startup or later when token becomes available.

    Args:
        app: FastAPI application instance
        state: Application state containing all components
    """
    # Get access token
    access_token = await FyersClientService.get_valid_access_token()

    # Initial symbol refresh
    logger.info("Performing initial symbol refresh...")
    symbol_refresh_manager = SymbolRefreshManager()
    refresh_results = await symbol_refresh_manager.refresh_symbols_for_all_instruments()

    # Get all symbols
    all_symbols = []
    for result in refresh_results.values():
        if result and result.get("symbols"):
            all_symbols.extend(result["symbols"])

    if not all_symbols:
        logger.warning("No symbols available for WebSocket subscription")
        return

    # Update market state with symbol mappings
    if refresh_results:
        first_result = next((r for r in refresh_results.values() if r), None)
        if first_result:
            symbol_to_strike, _ = build_symbol_to_strike_mapping(
                first_result.get("symbol_mapping", {})
            )
            state.market_state.update_symbol_mapping(
                symbol_to_strike, first_result.get("expiry_date")
            )

    # Start market data WebSocket
    await state.market_data_manager.start(access_token, all_symbols)

    # Create and start schedulers
    symbol_refresh_scheduler = get_symbol_refresh_scheduler(state)
    snapshot_merge_scheduler = get_snapshot_merge_scheduler(state)

    await symbol_refresh_scheduler.start()
    await snapshot_merge_scheduler.start()

    # Store schedulers for cleanup
    state._symbol_refresh_scheduler = symbol_refresh_scheduler
    state._snapshot_merge_scheduler = snapshot_merge_scheduler

    logger.info(
        f"Ingestion started - "
        f"symbols: {len(all_symbols)}, "
        f"broadcast_interval_ms: {LIVE_DATA_WEBSOCKET_BROADCAST_INTERVAL_MS}"
    )


async def _shutdown_app_state(app: FastAPI) -> None:
    """Shutdown all application components."""
    state: AppState = app.state.app_state

    if not state:
        return

    # Stop token watcher first
    if hasattr(state, "_token_watcher") and state._token_watcher:
        await state._token_watcher.stop()
        logger.info("Token watcher stopped")

    # Stop broadcaster
    if state.broadcaster:
        await state.broadcaster.stop()
        logger.info("WebSocket broadcaster stopped")

    # Stop market data manager
    if state.market_data_manager:
        await state.market_data_manager.stop()
        logger.info("Market data manager stopped")

    # Stop schedulers
    if hasattr(state, "_symbol_refresh_scheduler") and state._symbol_refresh_scheduler:
        await state._symbol_refresh_scheduler.stop()
        logger.info("Symbol refresh scheduler stopped")

    if hasattr(state, "_snapshot_merge_scheduler") and state._snapshot_merge_scheduler:
        await state._snapshot_merge_scheduler.stop()
        logger.info("Snapshot merge scheduler stopped")

    logger.info("Application shutdown complete")


def get_app_state(app: FastAPI) -> AppState:
    """Get application state from FastAPI app.

    Args:
        app: FastAPI application instance

    Returns:
        AppState: Application state container

    Raises:
        RuntimeError: If state is not initialized
    """
    if not hasattr(app.state, "app_state") or app.state.app_state is None:
        raise RuntimeError("Application state not initialized")
    return app.state.app_state
