from __future__ import annotations

import asyncio

from redis.asyncio import Redis
from redis.exceptions import RedisError

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.config.src.redis import (
    REDIS_ENABLED,
    REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
    REDIS_SOCKET_TIMEOUT_SECONDS,
    REDIS_URL,
)

log = CustomLogger("RedisClient")
logger, listener = log.get_logger()
listener.start()


class RedisClientManager:
    """Owns a lazily initialized shared Redis client with circuit breaker pattern."""

    def __init__(self) -> None:
        self._client: Redis | None = None
        self._lock = asyncio.Lock()
        self._healthy: bool = True
        self._consecutive_failures: int = 0
        self._max_consecutive_failures: int = 3

    @property
    def enabled(self) -> bool:
        return REDIS_ENABLED

    async def get_client(self) -> Redis:
        if not self.enabled:
            raise RuntimeError("Redis is disabled by configuration")

        if self._client is not None:
            return self._client

        async with self._lock:
            if self._client is None:
                self._client = self._create_client()
                logger.info("Redis client initialized")
        return self._client

    def _create_client(self, disable_health_check: bool = False) -> Redis:
        """Create a Redis client. Can disable health check to avoid recursion bug."""
        health_check = (
            0 if disable_health_check else REDIS_HEALTH_CHECK_INTERVAL_SECONDS
        )
        return Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            health_check_interval=health_check,
            retry_on_timeout=True,
        )

    async def safe_execute(self, operation, *args, **kwargs):
        """
        Execute a Redis operation with error handling and circuit breaker.
        Catches RecursionError from redis-py health check bug and recovers.
        """
        if (
            not self._healthy
            and self._consecutive_failures >= self._max_consecutive_failures
        ):
            raise RedisError(
                "Redis client is in circuit breaker state - too many failures"
            )

        try:
            client = await self.get_client()
            result = await operation(client, *args, **kwargs)
            self._consecutive_failures = 0
            self._healthy = True
            return result
        except RecursionError as e:
            # Known redis-py bug: health check during connection causes infinite recursion
            # when Redis server is unavailable. Reset client with health check disabled.
            logger.error(
                f"RecursionError in Redis operation (redis-py health check bug): {e}. "
                "Resetting client with health check disabled."
            )
            await self._reset_client(disable_health_check=True)
            raise RedisError(
                "Redis connection failed - server may be unavailable. "
                "Client reset with health check disabled for next attempt."
            ) from e
        except RedisError as e:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_consecutive_failures:
                self._healthy = False
                logger.error(
                    f"Redis client entering circuit breaker state after "
                    f"{self._consecutive_failures} consecutive failures - {str(e)}"
                )
            raise

    async def _reset_client(self, disable_health_check: bool = False) -> None:
        """Reset the Redis client, optionally with health check disabled."""
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.aclose()
                except Exception as e:
                    logger.warning(f"Error closing Redis client during reset: {e}")
            self._client = self._create_client(
                disable_health_check=disable_health_check
            )
            status = "disabled" if disable_health_check else "enabled"
            logger.info(f"Redis client reset (health check {status})")

    async def ping(self) -> bool:
        if not self.enabled:
            return False
        try:
            client = await self.get_client()
            response = await client.ping()
            return bool(response)
        except (RedisError, RecursionError) as e:
            logger.error(f"Redis ping failed: {e}")
            return False

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None
        self._healthy = True
        self._consecutive_failures = 0
        logger.info("Redis client closed")


redis_client_manager = RedisClientManager()
