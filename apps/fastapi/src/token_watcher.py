"""Token watcher for auto-starting ingestion when FYERS token becomes available."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.config.src.fyers import (
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    TOKEN_WATCHER_POLL_INTERVAL_SECONDS,
)

log = CustomLogger("TokenWatcher")
logger, listener = log.get_logger()
listener.start()

IST = ZoneInfo("Asia/Kolkata")


class TokenWatcher:
    """Background task that watches for FYERS token availability.

    Polls for token availability during market hours and automatically
    starts ingestion when a valid token is found. Also handles reconnection
    when tokens expire.
    """

    def __init__(self, app_state):
        """Initialize token watcher.

        Args:
            app_state: Application state containing ingestion status and components
        """
        self._app_state = app_state
        self._is_running = False
        self._poll_interval_seconds = TOKEN_WATCHER_POLL_INTERVAL_SECONDS
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the token watcher background task."""
        if self._is_running:
            logger.warning("Token watcher already running")
            return

        self._is_running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info("Token watcher started")

    async def stop(self) -> None:
        """Stop the token watcher."""
        if not self._is_running:
            return

        self._is_running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("Token watcher stopped")

    async def trigger_immediate_check(self) -> None:
        """Trigger immediate token check (called from callback)."""
        logger.info("Immediate token check triggered")
        await self._check_and_start_ingestion()

    def _is_market_hours(self) -> bool:
        """Check if current time is within market hours (IST).

        Returns:
            True if within market hours, False otherwise
        """
        now = datetime.now(IST)
        market_open = now.replace(
            hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0
        )
        market_close = now.replace(
            hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0
        )
        return market_open <= now <= market_close

    async def _watch_loop(self) -> None:
        """Main watch loop that polls for token availability."""
        while self._is_running:
            try:
                # Only check during market hours
                if self._is_market_hours():
                    await self._check_and_start_ingestion()
                else:
                    logger.debug("Outside market hours, skipping token check")

                await asyncio.sleep(self._poll_interval_seconds)

            except asyncio.CancelledError:
                break
            except Exception as error:
                logger.error(f"Token watcher error - error: {str(error)}")
                await asyncio.sleep(5)  # Brief delay before retry

    async def _check_and_start_ingestion(self) -> None:
        """Check if token available and start ingestion if needed."""
        # Skip if ingestion already running
        if self._app_state.is_ingestion_running():
            return

        async with self._app_state._ingestion_lock:
            # Double-check after acquiring lock
            if self._app_state.is_ingestion_running():
                return

            try:
                # Try to get token
                await FyersClientService.get_valid_access_token()

                logger.info("FYERS token found, starting ingestion")

                # Import here to avoid circular dependency
                from apps.fastapi.src.lifespan import start_ingestion

                # Start ingestion
                await start_ingestion(self._app_state._app, self._app_state)

                self._app_state.mark_ingestion_started()

                logger.info("Ingestion auto-started successfully")

            except ValueError:
                # Token not available yet
                logger.debug("FYERS token not available yet, will retry")
            except Exception as error:
                logger.error(f"Failed to auto-start ingestion - error: {str(error)}")


__all__ = ["TokenWatcher"]
