from typing import Callable, Dict, List


class Event:
    """Base event class."""

    def __init__(self, event_type: str, data: dict | None = None):
        self.event_type = event_type
        self.data = data or {}


class TokenRefreshedEvent(Event):
    """Event fired when FYERS token is refreshed."""

    def __init__(self, access_token: str, token_date: str):
        super().__init__(
            event_type="TOKEN_REFRESHED",
            data={"access_token": access_token, "token_date": token_date},
        )


class SymbolListRefreshedEvent(Event):
    """Event fired when symbol list is refreshed."""

    def __init__(self, symbols: list[str], expiry_date: str):
        super().__init__(
            event_type="SYMBOL_LIST_REFRESHED",
            data={"symbols": symbols, "expiry_date": expiry_date},
        )


class EventDispatcher:
    """Simple observer pattern event dispatcher."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Subscribe to an event type.

        Args:
            event_type: The event type to subscribe to
            callback: Callable that receives the event
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Unsubscribe from an event type.

        Args:
            event_type: The event type to unsubscribe from
            callback: The callback to remove
        """
        if event_type in self._subscribers:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)

    async def dispatch_async(self, event: Event) -> None:
        """Dispatch an event asynchronously to all subscribers.

        Args:
            event: The event to dispatch
        """
        if event.event_type in self._subscribers:
            for callback in self._subscribers[event.event_type]:
                if hasattr(callback, "__call__"):
                    # Check if callback is async
                    import asyncio

                    if asyncio.iscoroutinefunction(callback):
                        await callback(event)
                    else:
                        callback(event)

    def dispatch_sync(self, event: Event) -> None:
        """Dispatch an event synchronously to all subscribers.

        Args:
            event: The event to dispatch
        """
        if event.event_type in self._subscribers:
            for callback in self._subscribers[event.event_type]:
                if hasattr(callback, "__call__"):
                    callback(event)


# Global event dispatcher instance
_event_dispatcher: EventDispatcher | None = None


def get_event_dispatcher() -> EventDispatcher:
    """Get or create the global event dispatcher.

    Returns:
        EventDispatcher: The global event dispatcher instance
    """
    global _event_dispatcher
    if _event_dispatcher is None:
        _event_dispatcher = EventDispatcher()
    return _event_dispatcher
