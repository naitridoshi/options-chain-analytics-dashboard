from __future__ import annotations

from datetime import datetime, timezone

from libs.utils.config.src.redis import (
    REDIS_ENABLED,
    REDIS_LIVE_APP_HEARTBEAT_TTL_SECONDS,
)
from libs.utils.db.redis.src import (
    RedisLiveAppStatusStore,
    redis_client_manager,
)


class RuntimeStoreHealthService:
    @staticmethod
    async def get_status() -> dict:
        if not REDIS_ENABLED:
            return {
                "enabled": False,
                "healthy": False,
                "backend": "postgres",
                "message": "Redis runtime store is disabled.",
            }

        healthy = await redis_client_manager.ping()
        live_app_status = await RedisLiveAppStatusStore.get_status()
        live_app_healthy = _is_live_app_healthy(live_app_status)
        return {
            "enabled": True,
            "healthy": healthy,
            "backend": "redis",
            "live_app": live_app_status,
            "live_app_healthy": live_app_healthy,
            "message": "Redis runtime store is reachable."
            if healthy
            else "Redis ping failed.",
        }


def _is_live_app_healthy(live_app_status: dict | None) -> bool:
    if not live_app_status:
        return False
    updated_at = live_app_status.get("updated_at")
    if not updated_at:
        return False
    try:
        updated_at_dt = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    if updated_at_dt.tzinfo is None:
        updated_at_dt = updated_at_dt.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - updated_at_dt).total_seconds()
    if age_seconds > REDIS_LIVE_APP_HEARTBEAT_TTL_SECONDS:
        return False
    if not live_app_status.get("owns_lock"):
        return False
    details = live_app_status.get("details") or {}
    streaming = details.get("streaming") or {}
    if not streaming:
        return False
    return bool(
        live_app_status.get("phase") == "running"
        and streaming.get("running")
        and streaming.get("connected")
        and int(streaming.get("subscribed_symbols") or 0) > 0
    )
