from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.ingestion.platform.modules.option_chain_market_data.src import (
        OptionChainMarketDataManager,
    )
    from libs.utils.common.events.src import EventDispatcher
    from libs.utils.common.market_state.src import MarketStateManager
    from libs.utils.common.websocket.src.broadcaster import WebSocketBroadcaster


@dataclass
class AppState:
    """Centralized application state container.

    Holds all shared state for the application, enabling proper lifecycle
    management and dependency injection instead of global singletons.

    Attributes:
        market_state: In-memory market data state (LTP, avg_price, etc.)
        event_dispatcher: Event dispatcher for cross-component communication
        market_data_manager: WebSocket manager for real-time market data
        broadcaster: WebSocket broadcaster for live dashboard updates
    """

    market_state: "MarketStateManager | None" = None
    event_dispatcher: "EventDispatcher | None" = None
    market_data_manager: "OptionChainMarketDataManager | None" = None
    broadcaster: "WebSocketBroadcaster | None" = None

    # Internal schedulers (not typed to avoid circular imports)
    _symbol_refresh_scheduler: Any = None
    _snapshot_merge_scheduler: Any = None

    _initialized: bool = field(default=False, repr=False)

    def is_initialized(self) -> bool:
        """Check if state has been initialized."""
        return self._initialized

    def mark_initialized(self) -> None:
        """Mark state as initialized."""
        self._initialized = True


__all__ = ["AppState"]
