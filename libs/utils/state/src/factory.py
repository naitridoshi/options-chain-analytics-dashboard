from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.events.src import EventDispatcher
from libs.utils.common.market_state.src import MarketStateManager
from libs.utils.state.src import AppState

log = CustomLogger("AppStateFactory")
logger, listener = log.get_logger()
listener.start()


def create_app_state() -> AppState:
    """Create and initialize application state.

    Creates all required service instances and wires them together.
    This should be called once at application startup.

    Returns:
        AppState: Initialized application state container
    """
    state = AppState()

    # Initialize core services
    state.event_dispatcher = EventDispatcher()
    state.market_state = MarketStateManager()

    logger.info("Application state created")

    state.mark_initialized()
    return state


async def create_app_state_with_market_data() -> AppState:
    """Create app state with market data manager initialized.

    This is used by the ingestion service which needs the full
    market data stack including WebSocket manager.

    Returns:
        AppState: Initialized application state with market data manager
    """
    from apps.ingestion.platform.modules.option_chain_market_data.src import (
        OptionChainMarketDataManager,
    )

    state = create_app_state()

    # Initialize market data manager with dependencies
    state.market_data_manager = OptionChainMarketDataManager(
        market_state=state.market_state,
        event_dispatcher=state.event_dispatcher,
    )

    logger.info("Application state created with market data manager")

    return state
