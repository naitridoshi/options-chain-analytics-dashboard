from __future__ import annotations

from datetime import date

from libs.utils.config.src.redis import REDIS_KEY_PREFIX


def _join(*parts: str) -> str:
    return ":".join((REDIS_KEY_PREFIX, *parts))


def runtime_health_key() -> str:
    return _join("health")


def live_market_lock_key() -> str:
    return _join("locks", "live-market-data")


def fyers_token_key(token_date: date) -> str:
    return _join("fyers", "token", token_date.isoformat())


def live_symbol_key(symbol: str) -> str:
    return _join("live", "symbol", symbol)


def live_channel_key(instrument_symbol: str) -> str:
    return _join("live", "channel", instrument_symbol.upper())


def intraday_snapshot_key(
    instrument_symbol: str, trade_date: str, interval_ts: str
) -> str:
    return _join("snapshots", instrument_symbol.upper(), trade_date, interval_ts)


def intraday_timeline_key(instrument_symbol: str, trade_date: str) -> str:
    return _join("timelines", instrument_symbol.upper(), trade_date)


def intraday_latest_snapshot_pointer_key(
    instrument_symbol: str, trade_date: str
) -> str:
    return _join("latest", instrument_symbol.upper(), trade_date)


def previous_day_final_snapshot_key(instrument_symbol: str) -> str:
    return _join("previous-day", "final", instrument_symbol.upper())


def rollover_marker_key(marker_name: str, trade_date: str) -> str:
    return _join("rollover", marker_name, trade_date)


def websocket_ticket_key(ticket_id: str) -> str:
    return _join("websocket", "ticket", ticket_id)


def live_app_status_key() -> str:
    return _join("live-app", "status")
