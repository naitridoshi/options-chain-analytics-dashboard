import asyncio
import json
from typing import TYPE_CHECKING

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from libs.utils.common.custom_logger.src import CustomLogger

if TYPE_CHECKING:
    from libs.utils.common.market_state.src import MarketStateManager

log = CustomLogger("WebSocketBroadcaster", is_request=False)
logger, listener = log.get_logger()
listener.start()


class WebSocketBroadcaster:
    """Broadcasts live market data to connected WebSocket clients.

    Runs as a background task that periodically broadcasts the current
    market state to all connected dashboard clients.
    """

    def __init__(self):
        self._connections: list[WebSocket] = []
        self._market_state: "MarketStateManager | None" = None
        self._broadcast_task: asyncio.Task | None = None
        self._is_running = False
        self._broadcast_interval_ms: int = 1000
        self._lock = asyncio.Lock()

    async def start(
        self,
        market_state: "MarketStateManager",
        broadcast_interval_ms: int = 1000,
    ) -> None:
        """Start the broadcaster.

        Args:
            market_state: MarketStateManager instance to broadcast data from
            broadcast_interval_ms: Interval between broadcasts in milliseconds
        """
        if self._is_running:
            logger.warning("Broadcaster already running")
            return

        self._market_state = market_state
        self._broadcast_interval_ms = broadcast_interval_ms
        self._is_running = True

        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        logger.info(
            f"WebSocket broadcaster started - interval_ms: {broadcast_interval_ms}"
        )

    async def stop(self) -> None:
        """Stop the broadcaster."""
        if not self._is_running:
            return

        self._is_running = False

        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None

        # Close all connections
        async with self._lock:
            for connection in self._connections:
                try:
                    await connection.close()
                except Exception:
                    pass
            self._connections.clear()

        logger.info("WebSocket broadcaster stopped")

    async def connect(self, websocket: WebSocket) -> None:
        """Add a new WebSocket connection.

        Args:
            websocket: WebSocket connection to add
        """
        await websocket.accept()
        async with self._lock:
            self._connections.append(websocket)
        logger.info(f"WebSocket client connected - total: {len(self._connections)}")

        # Send initial state immediately
        if self._market_state:
            try:
                initial_data = self._get_market_data()
                await websocket.send_json(initial_data)
            except Exception as error:
                logger.error(f"Failed to send initial data - error: {str(error)}")

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection.

        Args:
            websocket: WebSocket connection to remove
        """
        async with self._lock:
            if websocket in self._connections:
                self._connections.remove(websocket)
        logger.info(f"WebSocket client disconnected - total: {len(self._connections)}")

    def _get_market_data(self) -> dict:
        """Get current market data for broadcast.

        Returns:
            dict: Market data in format expected by dashboard
        """
        if not self._market_state:
            return {"type": "market_data", "data": {}}

        symbols_data = {}
        for symbol, tick_data in self._market_state.get_all_symbols().items():
            symbols_data[symbol] = tick_data.to_dict()

        strikes_data = {}
        for strike, strike_data in self._market_state.get_all_strikes().items():
            strikes_data[strike] = strike_data.to_dict()

        return {
            "type": "market_data",
            "data": {
                "symbols": symbols_data,
                "strikes": strikes_data,
                "summary": self._market_state.get_state_summary(),
            },
        }

    def _is_connection_valid(self, websocket: WebSocket) -> bool:
        """Check if WebSocket connection is still valid.

        Args:
            websocket: WebSocket connection to check

        Returns:
            bool: True if connection is valid, False otherwise
        """
        try:
            return (
                websocket.client_state == WebSocketState.CONNECTED
                and websocket.application_state == WebSocketState.CONNECTED
            )
        except Exception:
            return False

    async def _safe_send(self, websocket: WebSocket, message: str) -> bool:
        """Safely send a message to a WebSocket client.

        Args:
            websocket: WebSocket connection to send to
            message: JSON message string to send

        Returns:
            bool: True if send was successful, False otherwise
        """
        try:
            # Check connection state before sending
            if not self._is_connection_valid(websocket):
                return False

            await websocket.send_text(message)
            return True
        except Exception:
            # Any error means the connection is no longer valid
            # This includes starlette_context errors when running in background tasks
            # and normal disconnection errors - all should be treated the same way
            return False

    async def _broadcast_loop(self) -> None:
        """Background task that broadcasts market data periodically."""
        while self._is_running:
            try:
                if self._connections and self._market_state:
                    data = self._get_market_data()
                    message = json.dumps(data)

                    # Send to all connections (copy list to avoid modification during iteration)
                    async with self._lock:
                        connections_snapshot = list(self._connections)

                    disconnected = []
                    for connection in connections_snapshot:
                        success = await self._safe_send(connection, message)
                        if not success:
                            disconnected.append(connection)

                    # Remove disconnected clients
                    for conn in disconnected:
                        await self.disconnect(conn)

                await asyncio.sleep(self._broadcast_interval_ms / 1000)

            except asyncio.CancelledError:
                break
            except Exception as error:
                logger.error(f"Broadcast loop error - error: {str(error)}")
                await asyncio.sleep(1)

    @property
    def connection_count(self) -> int:
        """Get number of connected clients."""
        return len(self._connections)

    @property
    def is_running(self) -> bool:
        """Check if broadcaster is running."""
        return self._is_running


# Global instance
_broadcaster: WebSocketBroadcaster | None = None


def get_broadcaster() -> WebSocketBroadcaster:
    """Get or create the global broadcaster instance."""
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = WebSocketBroadcaster()
    return _broadcaster
