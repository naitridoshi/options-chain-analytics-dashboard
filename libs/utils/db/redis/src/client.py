from __future__ import annotations

import asyncio

from redis.asyncio import Redis

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
    """Owns a lazily initialized shared Redis client."""

    def __init__(self) -> None:
        self._client: Redis | None = None
        self._lock = asyncio.Lock()

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
                self._client = Redis.from_url(
                    REDIS_URL,
                    decode_responses=True,
                    socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
                    socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
                    health_check_interval=REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
                    retry_on_timeout=True,
                )
                logger.info("Redis client initialized")
        return self._client

    async def ping(self) -> bool:
        if not self.enabled:
            return False
        client = await self.get_client()
        response = await client.ping()
        return bool(response)

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None
        logger.info("Redis client closed")


redis_client_manager = RedisClientManager()
