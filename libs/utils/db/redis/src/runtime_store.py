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
    REDIS_WEEKLY_CLOSE_TTL_SECONDS,
)
from libs.utils.db.redis.src.client import redis_client_manager
from libs.utils.db.redis.src.keys import (
    fyers_token_key,
    index_intraday_latest_snapshot_pointer_key,
    index_intraday_snapshot_key,
    index_intraday_timeline_key,
    index_intraday_trade_dates_key,
    intraday_latest_snapshot_pointer_key,
    intraday_snapshot_key,
    intraday_timeline_key,
    intraday_trade_dates_key,
    live_app_status_key,
    live_channel_key,
    live_symbol_key,
    live_underlying_key,
    previous_day_final_snapshot_key,
    rollover_marker_key,
    script_intraday_latest_snapshot_pointer_key,
    script_intraday_snapshot_key,
    script_intraday_timeline_key,
    script_intraday_trade_dates_key,
    websocket_ticket_key,
    weekly_expiry_close_key,
)

log = CustomLogger("RedisRuntimeStore")
logger, listener = log.get_logger()
listener.start()

_SAVE_INTRADAY_SNAPSHOT_LUA = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[4])
redis.call('EXPIRE', KEYS[2], ARGV[2])

local current_snapshot_key = redis.call('GET', KEYS[3])
if not current_snapshot_key then
  redis.call('SET', KEYS[3], KEYS[1], 'EX', ARGV[2])
  return 1
end

local current_interval_ts = string.match(current_snapshot_key, '([^:]+)$')
if (not current_interval_ts) or (ARGV[4] >= current_interval_ts) then
  redis.call('SET', KEYS[3], KEYS[1], 'EX', ARGV[2])
  return 1
end

return 0
"""

_CONSUME_TICKET_LUA = """
local payload = redis.call('GET', KEYS[1])
if not payload then
  return false
end
redis.call('DEL', KEYS[1])
return payload
"""


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
    # Track first write per symbol for debug logging (class-level to persist across instances)
    _first_write_logged: set[str] = set()

    @staticmethod
    async def write_live_symbol(
        *,
        instrument_symbol: str,
        symbol: str,
        payload: dict[str, Any],
    ) -> None:
        client = await redis_client_manager.get_client()
        exact_symbol = _symbol_storage_key(symbol)
        redis_key = live_symbol_key(exact_symbol)

        # VALIDATION: Check symbol integrity to prevent LTP cross-contamination
        payload_symbol = payload.get("symbol", "")
        payload_option_type = payload.get("option_type", "")

        # The symbol in payload should match the storage key
        if payload_symbol and payload_symbol != exact_symbol:
            logger.warning(
                f"Symbol mismatch in write_live_symbol - storage_key: {exact_symbol} - "
                f"payload_symbol: {payload_symbol} - "
                f"strike: {payload.get('strike_price')} type: {payload_option_type} - "
                f"This may indicate a mapping error"
            )

        # Check if option_type matches the symbol (CE should end with CE, PE should end with PE)
        if payload_option_type and payload_symbol:
            symbol_upper = payload_symbol.upper()
            if payload_option_type == "CE" and not symbol_upper.endswith("CE"):
                logger.error(
                    f"CRITICAL: Option type mismatch - type: CE but symbol ends with: "
                    f"{symbol_upper[-2:] if len(symbol_upper) >= 2 else symbol_upper} - "
                    f"symbol: {payload_symbol} - strike: {payload.get('strike_price')} - "
                    f"SKIPPING WRITE to prevent LTP cross-contamination"
                )
                return
            if payload_option_type == "PE" and not symbol_upper.endswith("PE"):
                logger.error(
                    f"CRITICAL: Option type mismatch - type: PE but symbol ends with: "
                    f"{symbol_upper[-2:] if len(symbol_upper) >= 2 else symbol_upper} - "
                    f"symbol: {payload_symbol} - strike: {payload.get('strike_price')} - "
                    f"SKIPPING WRITE to prevent LTP cross-contamination"
                )
                return

        # Log first write per symbol for debugging (avoid log spam)
        if exact_symbol not in RedisLiveMarketStore._first_write_logged:
            logger.info(
                f"Redis write_live_symbol - key: {redis_key} - "
                f"symbol: {symbol} - strike: {payload.get('strike_price')} - "
                f"type: {payload.get('option_type')} - ltp: {payload.get('ltp')}"
            )
            RedisLiveMarketStore._first_write_logged.add(exact_symbol)

        encoded = json.dumps(payload, separators=(",", ":"))
        await client.set(
            redis_key,
            encoded,
            ex=REDIS_LIVE_DATA_TTL_SECONDS,
        )
        await client.publish(live_channel_key(instrument_symbol), encoded)

    @staticmethod
    async def get_live_symbol(symbol: str) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        exact_symbol = _symbol_storage_key(symbol)
        payload = await client.get(live_symbol_key(exact_symbol))
        if not payload:
            payload = await client.get(live_symbol_key(_normalize_symbol_key(symbol)))
        return json.loads(payload) if payload else None

    @staticmethod
    async def get_live_symbols(symbols: list[str]) -> dict[str, dict[str, Any]]:
        requested_symbols = [symbol for symbol in symbols if symbol]
        if not requested_symbols:
            return {}

        client = await redis_client_manager.get_client()
        # Build list of unique symbols to query (preserve original case for storage key)
        exact_symbols: list[str] = []
        for symbol in requested_symbols:
            exact_symbol = _symbol_storage_key(symbol)
            if exact_symbol not in exact_symbols:
                exact_symbols.append(exact_symbol)

        keys = [live_symbol_key(symbol) for symbol in exact_symbols]
        values = await client.mget(keys)
        exact_payloads: dict[str, dict[str, Any]] = {}
        missing_symbols: list[str] = []
        for symbol, value in zip(exact_symbols, values, strict=False):
            if not value:
                missing_symbols.append(symbol)
                continue
            exact_payloads[symbol] = json.loads(value)

        # Fallback: try uppercase normalized keys for missing symbols
        normalized_payloads: dict[str, dict[str, Any]] = {}
        if missing_symbols:
            legacy_keys = [
                live_symbol_key(_normalize_symbol_key(symbol))
                for symbol in missing_symbols
            ]
            legacy_values = await client.mget(legacy_keys)
            for symbol, value in zip(missing_symbols, legacy_values, strict=False):
                if not value:
                    continue
                normalized_payloads[symbol] = json.loads(value)

        # Build final result map
        payloads: dict[str, dict[str, Any]] = {}
        for symbol in requested_symbols:
            exact_symbol = _symbol_storage_key(symbol)
            payload = exact_payloads.get(exact_symbol) or normalized_payloads.get(
                exact_symbol
            )
            if payload:
                payloads[symbol] = payload
        return payloads

    @staticmethod
    async def write_live_underlying(
        *,
        instrument_symbol: str,
        payload: dict[str, Any],
    ) -> None:
        client = await redis_client_manager.get_client()
        encoded = json.dumps(payload, separators=(",", ":"))
        await client.set(
            live_underlying_key(instrument_symbol),
            encoded,
            ex=REDIS_LIVE_DATA_TTL_SECONDS,
        )
        await client.publish(live_channel_key(instrument_symbol), encoded)

    @staticmethod
    async def get_live_underlying(instrument_symbol: str) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        payload = await client.get(live_underlying_key(instrument_symbol))
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
        trade_dates_key = intraday_trade_dates_key(instrument_symbol)
        latest_key = intraday_latest_snapshot_pointer_key(instrument_symbol, trade_date)
        encoded = json.dumps(payload, separators=(",", ":"))
        await client.eval(
            _SAVE_INTRADAY_SNAPSHOT_LUA,
            3,
            snapshot_key,
            timeline_key,
            latest_key,
            encoded,
            REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS,
            _to_epoch_score(interval_ts),
            interval_ts,
        )
        await client.sadd(trade_dates_key, trade_date)
        await client.expire(trade_dates_key, REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS)

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
        trade_dates_key = intraday_trade_dates_key(instrument_symbol)
        latest_key = intraday_latest_snapshot_pointer_key(instrument_symbol, trade_date)
        interval_ids = await client.zrange(timeline_key, 0, -1)
        snapshot_keys = [
            intraday_snapshot_key(instrument_symbol, trade_date, interval_id)
            for interval_id in interval_ids
        ]
        keys_to_delete = [timeline_key, latest_key, *snapshot_keys]
        removed = 0
        if keys_to_delete:
            removed = int(await client.delete(*keys_to_delete))
        await client.srem(trade_dates_key, trade_date)
        if removed == 0:
            return 0
        return removed

    @staticmethod
    async def list_trade_dates(instrument_symbol: str) -> list[str]:
        client = await redis_client_manager.get_client()
        trade_dates = await client.smembers(intraday_trade_dates_key(instrument_symbol))
        return sorted(trade_dates)


class RedisScriptSnapshotStore:
    @staticmethod
    async def save_intraday_snapshot(
        *,
        trade_date: str,
        interval_ts: str,
        payload: dict[str, Any],
    ) -> None:
        client = await redis_client_manager.get_client()
        snapshot_key = script_intraday_snapshot_key(trade_date, interval_ts)
        timeline_key = script_intraday_timeline_key(trade_date)
        trade_dates_key = script_intraday_trade_dates_key()
        latest_key = script_intraday_latest_snapshot_pointer_key(trade_date)
        encoded = json.dumps(payload, separators=(",", ":"))
        await client.eval(
            _SAVE_INTRADAY_SNAPSHOT_LUA,
            3,
            snapshot_key,
            timeline_key,
            latest_key,
            encoded,
            REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS,
            _to_epoch_score(interval_ts),
            interval_ts,
        )
        await client.sadd(trade_dates_key, trade_date)
        await client.expire(trade_dates_key, REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS)

    @staticmethod
    async def get_latest_snapshot(*, trade_date: str) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        latest_key = script_intraday_latest_snapshot_pointer_key(trade_date)
        snapshot_key = await client.get(latest_key)
        if not snapshot_key:
            return None
        payload = await client.get(snapshot_key)
        return json.loads(payload) if payload else None

    @staticmethod
    async def get_snapshot(
        *, trade_date: str, interval_ts: str
    ) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        payload = await client.get(
            script_intraday_snapshot_key(trade_date, interval_ts)
        )
        return json.loads(payload) if payload else None

    @staticmethod
    async def get_timeline(*, trade_date: str, limit: int) -> list[dict[str, Any]]:
        client = await redis_client_manager.get_client()
        timeline_key = script_intraday_timeline_key(trade_date)
        interval_ids = await client.zrevrange(timeline_key, 0, max(0, limit - 1))
        if not interval_ids:
            return []

        snapshot_keys = [
            script_intraday_snapshot_key(trade_date, interval_id)
            for interval_id in interval_ids
        ]
        values = await client.mget(snapshot_keys)
        return [json.loads(value) for value in values if value]

    @staticmethod
    async def list_trade_dates() -> list[str]:
        client = await redis_client_manager.get_client()
        trade_dates = await client.smembers(script_intraday_trade_dates_key())
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
        payload = await client.eval(_CONSUME_TICKET_LUA, 1, key)
        if not payload:
            return None
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


class RedisWeeklyCloseStore:
    """Store for weekly expiry close spot prices used in movement calculations."""

    @staticmethod
    async def save_weekly_close(
        *,
        instrument_symbol: str,
        expiry_date: str,
        close_spot: float,
        captured_at: str,
    ) -> None:
        """Save weekly expiry close spot price to Redis."""
        client = await redis_client_manager.get_client()
        payload = {
            "expiry_date": expiry_date,
            "close_spot": close_spot,
            "captured_at": captured_at,
            "day_of_week": "Tuesday",
        }
        await client.set(
            weekly_expiry_close_key(instrument_symbol),
            json.dumps(payload, separators=(",", ":")),
            ex=REDIS_WEEKLY_CLOSE_TTL_SECONDS,
        )

    @staticmethod
    async def get_weekly_close(instrument_symbol: str) -> dict[str, Any] | None:
        """Get weekly expiry close spot price from Redis."""
        client = await redis_client_manager.get_client()
        payload = await client.get(weekly_expiry_close_key(instrument_symbol))
        return json.loads(payload) if payload else None


def _to_epoch_score(interval_ts: str) -> float:
    return datetime.fromisoformat(interval_ts).timestamp()


def _normalize_symbol_key(symbol: str) -> str:
    """Normalize symbol for lookup - always uppercase."""
    return str(symbol).strip().upper()


def _symbol_storage_key(symbol: str) -> str:
    """
    Storage key for Redis - preserves original case to avoid data loss,
    but callers should normalize for lookup.
    """
    return str(symbol).strip()


class RedisIndexSnapshotStore:
    @staticmethod
    async def save_intraday_snapshot(
        *,
        trade_date: str,
        interval_ts: str,
        payload: dict[str, Any],
    ) -> None:
        client = await redis_client_manager.get_client()
        snapshot_key = index_intraday_snapshot_key(trade_date, interval_ts)
        timeline_key = index_intraday_timeline_key(trade_date)
        trade_dates_key = index_intraday_trade_dates_key()
        latest_key = index_intraday_latest_snapshot_pointer_key(trade_date)
        encoded = json.dumps(payload, separators=(",", ":"))
        await client.eval(
            _SAVE_INTRADAY_SNAPSHOT_LUA,
            3,
            snapshot_key,
            timeline_key,
            latest_key,
            encoded,
            REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS,
            _to_epoch_score(interval_ts),
            interval_ts,
        )
        await client.sadd(trade_dates_key, trade_date)
        await client.expire(trade_dates_key, REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS)

    @staticmethod
    async def get_latest_snapshot(*, trade_date: str) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        latest_key = index_intraday_latest_snapshot_pointer_key(trade_date)
        snapshot_key = await client.get(latest_key)
        if not snapshot_key:
            return None
        payload = await client.get(snapshot_key)
        return json.loads(payload) if payload else None

    @staticmethod
    async def get_snapshot(
        *, trade_date: str, interval_ts: str
    ) -> dict[str, Any] | None:
        client = await redis_client_manager.get_client()
        payload = await client.get(index_intraday_snapshot_key(trade_date, interval_ts))
        return json.loads(payload) if payload else None

    @staticmethod
    async def list_trade_dates() -> list[str]:
        client = await redis_client_manager.get_client()
        trade_dates = await client.smembers(index_intraday_trade_dates_key())
        return sorted(trade_dates)
