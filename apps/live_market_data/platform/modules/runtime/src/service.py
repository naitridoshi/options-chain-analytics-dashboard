from __future__ import annotations

from datetime import datetime, timezone

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.runtime_store.src import RuntimeStoreHealthService
from libs.utils.db.redis.src import (
    RedisLiveAppStatusStore,
    RedisLockHandle,
    RedisLockManager,
    live_market_lock_key,
    redis_client_manager,
)

log = CustomLogger("LiveMarketDataRuntimeService")
logger, listener = log.get_logger()
listener.start()


class LiveMarketDataRuntimeService:
    """Owns process-level runtime resources for the live data app."""

    def __init__(self) -> None:
        self._lock_handle: RedisLockHandle | None = None

    async def start(self) -> None:
        status = await RuntimeStoreHealthService.get_status()
        if not status["healthy"]:
            raise RuntimeError(status["message"])

        self._lock_handle = await RedisLockManager.acquire(live_market_lock_key())
        if self._lock_handle is None:
            raise RuntimeError(
                "Another live market data process already owns the runtime lock."
            )

        await self._write_heartbeat("starting")
        logger.info("Live market data runtime service started")

    async def stop(self) -> None:
        await self._write_heartbeat("stopped")
        await RedisLockManager.release(self._lock_handle)
        self._lock_handle = None
        await redis_client_manager.close()
        logger.info("Live market data runtime service stopped")

    async def get_status(self) -> dict:
        runtime_status = await RuntimeStoreHealthService.get_status()
        runtime_status["owns_lock"] = self._lock_handle is not None
        return runtime_status

    async def heartbeat(self, phase: str, details: dict | None = None) -> None:
        await self._write_heartbeat(phase, details=details)

    async def _write_heartbeat(self, phase: str, details: dict | None = None) -> None:
        payload = {
            "phase": phase,
            "owns_lock": self._lock_handle is not None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if details:
            payload["details"] = details
        await RedisLiveAppStatusStore.write_status(payload)
