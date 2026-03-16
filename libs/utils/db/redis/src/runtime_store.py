from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.config.src.redis import (
    REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS,
    REDIS_LIVE_APP_HEARTBEAT_TTL_SECONDS,
    REDIS_LIVE_DATA_TTL_SECONDS,
    REDIS_PREVIOUS_DAY_SNAPSHOT_TTL_SECONDS,
    REDIS_TOKEN_TTL_SECONDS,
    REDIS_WEBSOCKET_TICKET_TTL_SECONDS,
)
from libs.utils.db.redis.src.client import redis_client_manager
from libs.utils.db.redis.src.keys import (
    fyers_token_key,
    intraday_latest_snapshot_pointer_key,
    intraday_snapshot_key,
    intraday_timeline_key,
    live_app_status_key,
    live_channel_key,
    live_symbol_key,
    previous_day_final_snapshot_key,
    rollover_marker_key,
    websocket_ticket_key,
)

log = CustomLogger("RedisRuntimeStore")
logger, listener = log.get_logger()
listener.start()


@dataclass
class RuntimeFyersToken:
    access_token: str
    token_date: str
    created_at: str
    expires_at: str | None = None
    source: str = "redis"


class RedisTokenStore:
    @staticmethod
    async def get_token_for_date(token_date: date) -> RuntimeFyersToken | None:
        client = await redis_client_manager.get_client()
        payload = await client.get(fyers_token_key(token_date))
        if not payload:
            return None
        data = json.loads(payload)
        return RuntimeFyersToken(**data)

    @staticmethod
    async def upsert_token(
        *,
        token_date: date,
        access_token: str,
        expires_at: datetime | None = None,
    ) -> RuntimeFyersToken:
        client = await redis_client_manager.get_client()
        model = RuntimeFyersToken(
            access_token=access_token,
            token_date=token_date.isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=expires_at.isoformat() if expires_at else None,
        )
        await client.set(
            fyers_token_key(token_date),
            json.dumps(asdict(model)),
            ex=REDIS_TOKEN_TTL_SECONDS,
        )
        return model


class RedisLiveMarketStore:
    @staticmethod
    async def write_live_symbol(
        *,
        instrument_symbol: str,
        symbol: str,
        payload: dict[str, Any],
    ) -> None:
        client = await redis_client_manager.get_client()
        encoded = json.dumps(payload, separators=(",", ":"))
        await client.set(
            live_symbol_key(symbol), encoded, ex=REDIS_LIVE_DATA_TTL_SECONDS
        )
        await client.publish(live_channel_key(instrument_symbol), encoded)

    @staticmethod
    async def get_live_symbol(symbol: str) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        payload = await client.get(live_symbol_key(symbol))
        return json.loads(payload) if payload else None


class RedisOptionChainSnapshotStore:
    @staticmethod
    async def save_intraday_snapshot(
        *,
        instrument_symbol: str,
        trade_date: str,
        interval_ts: str,
        payload: dict[str, Any],
    ) -> None:
        client = await redis_client_manager.get_client()
        snapshot_key = intraday_snapshot_key(instrument_symbol, trade_date, interval_ts)
        timeline_key = intraday_timeline_key(instrument_symbol, trade_date)
        latest_key = intraday_latest_snapshot_pointer_key(instrument_symbol, trade_date)
        encoded = json.dumps(payload, separators=(",", ":"))
        async with client.pipeline(transaction=True) as pipe:
            await (
                pipe.set(snapshot_key, encoded, ex=REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS)
                .zadd(timeline_key, {interval_ts: _to_epoch_score(interval_ts)})
                .expire(timeline_key, REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS)
                .set(latest_key, snapshot_key, ex=REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS)
                .execute()
            )

    @staticmethod
    async def get_latest_snapshot(
        *,
        instrument_symbol: str,
        trade_date: str,
    ) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        latest_key = intraday_latest_snapshot_pointer_key(instrument_symbol, trade_date)
        snapshot_key = await client.get(latest_key)
        if not snapshot_key:
            return None
        payload = await client.get(snapshot_key)
        return json.loads(payload) if payload else None

    @staticmethod
    async def get_snapshot(
        *,
        instrument_symbol: str,
        trade_date: str,
        interval_ts: str,
    ) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        payload = await client.get(
            intraday_snapshot_key(instrument_symbol, trade_date, interval_ts)
        )
        return json.loads(payload) if payload else None

    @staticmethod
    async def get_timeline(
        *,
        instrument_symbol: str,
        trade_date: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        client = await redis_client_manager.get_client()
        timeline_key = intraday_timeline_key(instrument_symbol, trade_date)
        interval_ids = await client.zrevrange(timeline_key, 0, max(0, limit - 1))
        if not interval_ids:
            return []

        snapshot_keys = [
            intraday_snapshot_key(instrument_symbol, trade_date, interval_id)
            for interval_id in interval_ids
        ]
        values = await client.mget(snapshot_keys)
        return [json.loads(value) for value in values if value]

    @staticmethod
    async def save_previous_day_final_snapshot(
        *,
        instrument_symbol: str,
        payload: dict[str, Any],
    ) -> None:
        client = await redis_client_manager.get_client()
        await client.set(
            previous_day_final_snapshot_key(instrument_symbol),
            json.dumps(payload, separators=(",", ":")),
            ex=REDIS_PREVIOUS_DAY_SNAPSHOT_TTL_SECONDS,
        )

    @staticmethod
    async def get_previous_day_final_snapshot(
        instrument_symbol: str,
    ) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        payload = await client.get(previous_day_final_snapshot_key(instrument_symbol))
        return json.loads(payload) if payload else None

    @staticmethod
    async def delete_trade_date(
        *,
        instrument_symbol: str,
        trade_date: str,
    ) -> int:
        client = await redis_client_manager.get_client()
        timeline_key = intraday_timeline_key(instrument_symbol, trade_date)
        latest_key = intraday_latest_snapshot_pointer_key(instrument_symbol, trade_date)
        interval_ids = await client.zrange(timeline_key, 0, -1)
        snapshot_keys = [
            intraday_snapshot_key(instrument_symbol, trade_date, interval_id)
            for interval_id in interval_ids
        ]
        keys_to_delete = [timeline_key, latest_key, *snapshot_keys]
        if not keys_to_delete:
            return 0
        return int(await client.delete(*keys_to_delete))

    @staticmethod
    async def list_trade_dates(instrument_symbol: str) -> list[str]:
        client = await redis_client_manager.get_client()
        pattern = intraday_timeline_key(instrument_symbol, "*")
        trade_dates: set[str] = set()
        async for key in client.scan_iter(match=pattern):
            parts = key.split(":")
            if parts:
                trade_dates.add(parts[-1])
        return sorted(trade_dates)


class RedisRolloverStore:
    @staticmethod
    async def is_marker_set(marker_name: str, trade_date: str) -> bool:
        client = await redis_client_manager.get_client()
        value = await client.get(rollover_marker_key(marker_name, trade_date))
        return value == "1"

    @staticmethod
    async def set_marker(marker_name: str, trade_date: str) -> None:
        client = await redis_client_manager.get_client()
        await client.set(rollover_marker_key(marker_name, trade_date), "1")


class RedisWebSocketTicketStore:
    @staticmethod
    async def create_ticket(*, subject: str, symbol: str) -> str:
        client = await redis_client_manager.get_client()
        ticket = secrets.token_urlsafe(24)
        payload = json.dumps(
            {
                "subject": subject,
                "symbol": symbol.upper(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            separators=(",", ":"),
        )
        await client.set(
            websocket_ticket_key(ticket),
            payload,
            ex=REDIS_WEBSOCKET_TICKET_TTL_SECONDS,
        )
        return ticket

    @staticmethod
    async def consume_ticket(ticket: str) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        key = websocket_ticket_key(ticket)
        payload = await client.get(key)
        if not payload:
            return None
        await client.delete(key)
        return json.loads(payload)


class RedisLiveAppStatusStore:
    @staticmethod
    async def write_status(payload: dict[str, Any]) -> None:
        client = await redis_client_manager.get_client()
        await client.set(
            live_app_status_key(),
            json.dumps(payload, separators=(",", ":")),
            ex=REDIS_LIVE_APP_HEARTBEAT_TTL_SECONDS,
        )

    @staticmethod
    async def get_status() -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        payload = await client.get(live_app_status_key())
        return json.loads(payload) if payload else None


def _to_epoch_score(interval_ts: str) -> float:
    return datetime.fromisoformat(interval_ts).timestamp()
