from __future__ import annotations

import asyncio
from time import monotonic

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.config.src.redis import (
    REDIS_ENABLED,
    REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
    REDIS_MAX_CONNECTIONS_PER_CLIENT,
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
        self._circuit_open_at: float = 0.0
        self._circuit_cooldown_seconds: int = 30

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
        pool = ConnectionPool.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            health_check_interval=health_check,
            retry_on_timeout=True,
            max_connections=REDIS_MAX_CONNECTIONS_PER_CLIENT,
        )
        return Redis(connection_pool=pool)

    async def safe_execute(self, operation, *args, **kwargs):
        """
        Execute a Redis operation with error handling and circuit breaker.
        Catches RecursionError from redis-py health check bug and recovers.
        Auto-recovers after a cooldown period.
        """
        # Circuit breaker with auto-recovery cooldown
        if (
            not self._healthy
            and self._consecutive_failures >= self._max_consecutive_failures
        ):
            elapsed = monotonic() - self._circuit_open_at
            if elapsed < self._circuit_cooldown_seconds:
                raise RedisError(
                    f"Redis circuit breaker active - retrying in "
                    f"{self._circuit_cooldown_seconds - elapsed:.0f}s"
                )
            logger.info(
                "Redis circuit breaker cooldown elapsed - attempting recovery probe"
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
                if self._healthy:
                    # First time entering circuit breaker state - reset client
                    self._healthy = False
                    self._circuit_open_at = monotonic()
                    await self._reset_client()
                    logger.error(
                        f"Redis client entering circuit breaker state after "
                        f"{self._consecutive_failures} consecutive failures - {str(e)}"
                    )
                else:
                    # Already in circuit breaker, probe failed - reset cooldown
                    self._circuit_open_at = monotonic()
            raise

    async def _reset_client(self, disable_health_check: bool = False) -> None:
        """Reset the Redis client, optionally with health check disabled."""
        async with self._lock:
            old = self._client
            self._client = None
            if old is not None:
                try:
                    await old.connection_pool.disconnect()
                except Exception as e:
                    logger.warning(f"Error disconnecting pool during reset: {e}")
                try:
                    await old.aclose()
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
        old = self._client
        self._client = None
        self._healthy = True
        self._consecutive_failures = 0
        try:
            await old.connection_pool.disconnect()
        except Exception as e:
            logger.warning(f"Error disconnecting pool during close: {e}")
        try:
            await old.aclose()
        except Exception as e:
            logger.warning(f"Error closing Redis client during close: {e}")
        logger.info("Redis client closed")


redis_client_manager = RedisClientManager()
