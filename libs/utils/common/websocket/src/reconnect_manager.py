import asyncio
from dataclasses import dataclass
from time import time

from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("WebSocketReconnectManager")
logger, listener = log.get_logger()
listener.start()


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    initial_delay: float = 1.0  # 1 second
    max_delay: float = 60.0  # 60 seconds
    backoff_factor: float = 2.0  # exponential multiplier
    jitter: bool = True  # Add randomness


class WebSocketReconnectManager:
    """Manages WebSocket reconnection with exponential backoff."""

    def __init__(self, config: RetryConfig | None = None):
        self.config = config or RetryConfig()
        self._attempt = 0
        self._last_retry_time: float | None = None
        self._connected = False

    async def get_retry_delay(self) -> float:
        """Calculate delay for next retry attempt using exponential backoff.

        Retry schedule (default config):
        Attempt 1: 1s
        Attempt 2: 2s
        Attempt 3: 4s
        Attempt 4: 8s
        Attempt 5: 16s
        Attempt 6+: 60s (capped)

        Returns:
            float: Delay in seconds
        """
        delay = self.config.initial_delay * (self.config.backoff_factor**self._attempt)
        delay = min(delay, self.config.max_delay)

        if self.config.jitter:
            import random

            jitter = random.uniform(0, delay * 0.1)
            delay += jitter

        return delay

    async def wait_before_retry(self) -> None:
        """Wait before next retry attempt."""
        self._attempt += 1
        delay = await self.get_retry_delay()

        logger.warning(
            f"WebSocket reconnect - attempt: {self._attempt}, wait_seconds: {delay:.2f}"
        )

        self._last_retry_time = time()
        await asyncio.sleep(delay)

    def on_connect_success(self) -> None:
        """Mark successful connection."""
        if self._attempt > 0:
            logger.info(
                f"WebSocket reconnected successfully - attempts: {self._attempt}"
            )
        self._attempt = 0
        self._connected = True

    def on_connect_failure(self) -> None:
        """Mark connection failure."""
        self._connected = False

    def reset(self) -> None:
        """Reset retry state."""
        self._attempt = 0
        self._last_retry_time = None
        self._connected = False
        logger.info("Reconnect manager reset")

    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._connected

    def get_attempt_count(self) -> int:
        """Get current attempt count."""
        return self._attempt

    def get_last_retry_time(self) -> float | None:
        """Get timestamp of last retry."""
        return self._last_retry_time
