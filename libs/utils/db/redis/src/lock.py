from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.config.src.redis import REDIS_LOCK_TTL_SECONDS
from libs.utils.db.redis.src.client import redis_client_manager

log = CustomLogger("RedisLockManager")
logger, listener = log.get_logger()
listener.start()


@dataclass
class RedisLockHandle:
    key: str
    value: str = field(default_factory=lambda: uuid.uuid4().hex)
    ttl_seconds: int = REDIS_LOCK_TTL_SECONDS


class RedisLockManager:
    @staticmethod
    async def acquire(
        key: str, ttl_seconds: int = REDIS_LOCK_TTL_SECONDS
    ) -> RedisLockHandle | None:
        client = await redis_client_manager.get_client()
        handle = RedisLockHandle(key=key, ttl_seconds=ttl_seconds)
        acquired = await client.set(
            key,
            handle.value,
            ex=ttl_seconds,
            nx=True,
        )
        if not acquired:
            return None
        logger.info(f"Redis lock acquired - key: {key}")
        return handle

    @staticmethod
    async def release(handle: RedisLockHandle | None) -> bool:
        if handle is None:
            return False
        client = await redis_client_manager.get_client()
        released = await client.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            handle.key,
            handle.value,
        )
        if released:
            logger.info(f"Redis lock released - key: {handle.key}")
        return bool(released)
