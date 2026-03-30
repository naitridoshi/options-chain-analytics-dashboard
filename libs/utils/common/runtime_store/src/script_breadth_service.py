from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from libs.platform.modules.option_chain_snapshot.src import (
    normalize_interval_boundary,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.script_catalog.src import ScriptCatalogService
from libs.utils.config.src.fyers import (
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
)
from libs.utils.db.redis.src import RedisScriptSnapshotStore

IST = ZoneInfo("Asia/Kolkata")
log = CustomLogger("RuntimeScriptSnapshotService")
logger, listener = log.get_logger()
listener.start()


@dataclass
class RuntimeScriptSnapshotPayload:
    market_date: str
    refresh_seconds: int
    captured_at: str
    advance_count: int
    decline_count: int
    unchanged_count: int
    total_scripts: int
    scripts: list[dict]

    def to_dict(self) -> dict:
        return {
            "market_date": self.market_date,
            "refresh_seconds": self.refresh_seconds,
            "captured_at": self.captured_at,
            "advance_count": self.advance_count,
            "decline_count": self.decline_count,
            "unchanged_count": self.unchanged_count,
            "total_scripts": self.total_scripts,
            "scripts": self.scripts,
        }


class RuntimeScriptSnapshotService:
    @classmethod
    async def save_intraday_snapshot(
        cls,
        *,
        captured_at: datetime,
        script_rows: list[dict],
    ) -> None:
        payload = cls.build_runtime_payload(
            captured_at=captured_at,
            script_rows=script_rows,
        )
        await RedisScriptSnapshotStore.save_intraday_snapshot(
            trade_date=payload.market_date,
            interval_ts=payload.captured_at,
            payload=payload.to_dict(),
        )

    @staticmethod
    def build_runtime_payload(
        *,
        captured_at: datetime,
        script_rows: list[dict],
    ) -> RuntimeScriptSnapshotPayload:
        advance = 0
        decline = 0
        unchanged = 0

        normalized_scripts: list[dict] = []
        for row in sorted(script_rows, key=lambda item: item["symbol"]):
            change = row.get("change")
            trend = "UNCHANGED"
            if change is not None and change > 0:
                trend = "ADVANCE"
                advance += 1
            elif change is not None and change < 0:
                trend = "DECLINE"
                decline += 1
            else:
                unchanged += 1

            normalized_scripts.append(
                {
                    "symbol": row["symbol"],
                    "name": row.get("name") or row["symbol"],
                    "fyers_symbol": row["fyers_symbol"],
                    "ltp": _as_number(row.get("ltp")),
                    "previous_close": _as_number(row.get("previous_close")),
                    "change": _as_number(change),
                    "change_pct": _as_number(row.get("change_pct")),
                    "trend": trend,
                }
            )

        normalized = normalize_interval_boundary(
            captured_at,
            interval_seconds=SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
        )
        market_date = normalized.astimezone(IST).date().isoformat()
        return RuntimeScriptSnapshotPayload(
            market_date=market_date,
            refresh_seconds=SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
            captured_at=normalized.isoformat(),
            advance_count=advance,
            decline_count=decline,
            unchanged_count=unchanged,
            total_scripts=len(normalized_scripts),
            scripts=normalized_scripts,
        )

    @classmethod
    async def get_previous_close_reference_map(
        cls,
        *,
        captured_at_utc: datetime,
    ) -> dict[str, float | None]:
        current_trade_date = captured_at_utc.astimezone(IST).date().isoformat()
        trade_dates = await RedisScriptSnapshotStore.list_trade_dates()
        previous_trade_dates = [
            item for item in trade_dates if item < current_trade_date
        ]
        if not previous_trade_dates:
            return {}

        previous_trade_date = previous_trade_dates[-1]
        exact_close_utc = datetime.combine(
            datetime.fromisoformat(previous_trade_date).date(),
            time(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE),
            tzinfo=IST,
        ).astimezone(timezone.utc)
        selected_snapshot = await RedisScriptSnapshotStore.get_snapshot(
            trade_date=previous_trade_date,
            interval_ts=exact_close_utc.isoformat(),
        )
        if not selected_snapshot:
            selected_snapshot = await RedisScriptSnapshotStore.get_latest_snapshot(
                trade_date=previous_trade_date
            )
        if not selected_snapshot:
            return {}

        resolved: dict[str, float | None] = {}
        for item in selected_snapshot.get("scripts", []):
            symbol = item.get("symbol")
            if not symbol:
                continue
            resolved[symbol] = item.get("ltp")
        return resolved

    @classmethod
    async def get_latest_advance_decline(cls) -> dict:
        trade_date = datetime.now(IST).date().isoformat()
        try:
            latest_snapshot = await RedisScriptSnapshotStore.get_latest_snapshot(
                trade_date=trade_date
            )
            if latest_snapshot:
                return latest_snapshot
        except Exception as e:
            logger.warning(f"Failed to get latest snapshot from Redis: {e}")

        active_scripts = ScriptCatalogService.get_active_scripts()
        return {
            "market_date": trade_date,
            "refresh_seconds": SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
            "captured_at": None,
            "advance_count": 0,
            "decline_count": 0,
            "unchanged_count": len(active_scripts),
            "total_scripts": len(active_scripts),
            "scripts": [
                {
                    "symbol": item.symbol,
                    "name": item.name or item.symbol,
                    "fyers_symbol": item.fyers_symbol,
                    "ltp": None,
                    "previous_close": None,
                    "change": None,
                    "change_pct": None,
                    "trend": "UNCHANGED",
                }
                for item in active_scripts
            ],
        }

    @classmethod
    async def get_latest_captured_at_for_today_ist(cls) -> datetime | None:
        trade_date = datetime.now(IST).date().isoformat()
        latest_snapshot = await RedisScriptSnapshotStore.get_latest_snapshot(
            trade_date=trade_date
        )
        if not latest_snapshot:
            return None
        captured_at = latest_snapshot.get("captured_at")
        if not captured_at:
            return None
        return datetime.fromisoformat(captured_at)


def _as_number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value
