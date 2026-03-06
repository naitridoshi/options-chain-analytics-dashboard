from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from libs.utils.config.src.fyers import MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE
from libs.utils.db.postgres.models.src.script_price_snapshot import (
    ScriptPriceSnapshot,
)
from libs.utils.db.postgres.operations.src.base import BaseOperations
from libs.utils.db.postgres.src.connection import postgres_connection
from libs.utils.db.postgres.src.repository import (
    get_script_price_snapshots_repository,
    get_scripts_repository,
)

IST = ZoneInfo("Asia/Kolkata")


class ScriptSnapshotOperations(BaseOperations[ScriptPriceSnapshot]):
    @staticmethod
    def _as_number(value: Decimal | int | float | None):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return float(value)
        return value

    @classmethod
    async def create_script_snapshots_bulk(
        cls,
        *,
        snapshots: list[dict],
    ) -> int:
        values: list[dict] = []
        for item in snapshots:
            previous_close = item.get("previous_close")
            ltp = item["ltp"]
            change = None
            change_pct = None
            if previous_close is not None:
                change = ltp - previous_close
                if previous_close != 0:
                    change_pct = (change / previous_close) * Decimal("100")
            values.append(
                {
                    "script_id": item["script_id"],
                    "captured_at": item["captured_at"],
                    "ltp": ltp,
                    "previous_close": previous_close,
                    "change": change,
                    "change_pct": change_pct,
                }
            )
        async with postgres_connection.get_session() as session:
            if values:
                stmt = insert(ScriptPriceSnapshot).values(values)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_script_snapshot_captured",
                    set_={
                        "ltp": stmt.excluded.ltp,
                        "previous_close": stmt.excluded.previous_close,
                        "change": stmt.excluded.change,
                        "change_pct": stmt.excluded.change_pct,
                        "updated_at": func.now(),
                    },
                )
                await session.execute(stmt)
            return len(values)

    @classmethod
    async def get_latest_captured_at_for_today_ist(cls) -> datetime | None:
        now_ist = datetime.now(IST)
        start_utc = datetime.combine(now_ist.date(), time.min, tzinfo=IST).astimezone(
            timezone.utc
        )
        end_utc = (
            datetime.combine(now_ist.date(), time.min, tzinfo=IST) + timedelta(days=1)
        ).astimezone(timezone.utc)

        async with postgres_connection.get_session() as session:
            repo = get_script_price_snapshots_repository(session)
            rows = await repo.list_ordered(
                where=[
                    repo.model.captured_at >= start_utc,
                    repo.model.captured_at < end_utc,
                ],
                order_by=desc(repo.model.captured_at),
                limit=1,
            )
            if not rows:
                return None
            return rows[0].captured_at

    @classmethod
    async def get_previous_close_reference_map_for_scripts(
        cls, *, script_ids: list, captured_at_utc: datetime
    ) -> dict:
        if not script_ids:
            return {}
        captured_at_ist = captured_at_utc.astimezone(IST)
        day_start_utc = datetime.combine(
            captured_at_ist.date(), time.min, tzinfo=IST
        ).astimezone(timezone.utc)

        async with postgres_connection.get_session() as session:
            latest_before_today_subq = (
                select(
                    ScriptPriceSnapshot.script_id.label("script_id"),
                    func.max(ScriptPriceSnapshot.captured_at).label("max_captured_at"),
                )
                .where(
                    ScriptPriceSnapshot.script_id.in_(script_ids),
                    ScriptPriceSnapshot.captured_at < day_start_utc,
                )
                .group_by(ScriptPriceSnapshot.script_id)
                .subquery()
            )
            latest_before_today_stmt = select(ScriptPriceSnapshot).join(
                latest_before_today_subq,
                (ScriptPriceSnapshot.script_id == latest_before_today_subq.c.script_id)
                & (
                    ScriptPriceSnapshot.captured_at
                    == latest_before_today_subq.c.max_captured_at
                ),
            )
            latest_before_today_result = await session.execute(latest_before_today_stmt)
            latest_before_today_rows = latest_before_today_result.scalars().all()
            fallback_row_map = {row.script_id: row for row in latest_before_today_rows}

            exact_pairs = []
            for row in latest_before_today_rows:
                prior_market_date_ist = row.captured_at.astimezone(IST).date()
                exact_close_ist = datetime.combine(
                    prior_market_date_ist,
                    time(
                        hour=MARKET_CLOSE_HOUR,
                        minute=MARKET_CLOSE_MINUTE,
                        second=0,
                    ),
                    tzinfo=IST,
                )
                exact_pairs.append(
                    (row.script_id, exact_close_ist.astimezone(timezone.utc))
                )

            exact_map = {}
            if exact_pairs:
                exact_close_stmt = select(ScriptPriceSnapshot).where(
                    tuple_(
                        ScriptPriceSnapshot.script_id,
                        ScriptPriceSnapshot.captured_at,
                    ).in_(exact_pairs)
                )
                exact_close_result = await session.execute(exact_close_stmt)
                exact_close_rows = exact_close_result.scalars().all()
                exact_map = {row.script_id: row.ltp for row in exact_close_rows}

            # Exact close takes precedence; otherwise fallback to latest prior snapshot.
            resolved = {}
            for script_id in script_ids:
                fallback_row = fallback_row_map.get(script_id)
                fallback_ltp = fallback_row.ltp if fallback_row else None
                resolved[script_id] = exact_map.get(script_id, fallback_ltp)
            return resolved

    @classmethod
    async def get_latest_advance_decline(cls) -> dict:
        async with postgres_connection.get_session() as session:
            scripts_repo = get_scripts_repository(session)

            active_scripts = await scripts_repo.list_ordered(
                where=scripts_repo.model.is_active.is_(True),
                order_by=scripts_repo.model.symbol.asc(),
                limit=5000,
            )
            if not active_scripts:
                return {
                    "captured_at": None,
                    "advance_count": 0,
                    "decline_count": 0,
                    "unchanged_count": 0,
                    "total_scripts": 0,
                    "scripts": [],
                }

            now_ist = datetime.now(IST)
            day_start_utc = datetime.combine(
                now_ist.date(), time.min, tzinfo=IST
            ).astimezone(timezone.utc)
            day_end_utc = (
                datetime.combine(now_ist.date(), time.min, tzinfo=IST)
                + timedelta(days=1)
            ).astimezone(timezone.utc)

            latest_subq = (
                select(
                    ScriptPriceSnapshot.script_id.label("script_id"),
                    func.max(ScriptPriceSnapshot.captured_at).label("max_captured_at"),
                )
                .where(
                    ScriptPriceSnapshot.captured_at >= day_start_utc,
                    ScriptPriceSnapshot.captured_at < day_end_utc,
                )
                .group_by(ScriptPriceSnapshot.script_id)
                .subquery()
            )

            latest_stmt = select(ScriptPriceSnapshot).join(
                latest_subq,
                (ScriptPriceSnapshot.script_id == latest_subq.c.script_id)
                & (ScriptPriceSnapshot.captured_at == latest_subq.c.max_captured_at),
            )
            latest_result = await session.execute(latest_stmt)
            latest_rows = latest_result.scalars().all()
            latest_by_script_id = {row.script_id: row for row in latest_rows}

            scripts_data = []
            advance = 0
            decline = 0
            unchanged = 0
            latest_captured_at = None

            for script in active_scripts:
                row = latest_by_script_id.get(script.id)
                if not row:
                    scripts_data.append(
                        {
                            "symbol": script.symbol,
                            "fyers_symbol": script.fyers_symbol,
                            "captured_at": None,
                            "ltp": None,
                            "previous_close": None,
                            "change": None,
                            "change_pct": None,
                            "trend": "UNCHANGED",
                        }
                    )
                    unchanged += 1
                    continue

                if latest_captured_at is None or row.captured_at > latest_captured_at:
                    latest_captured_at = row.captured_at

                change = cls._as_number(row.change)
                trend = "UNCHANGED"
                if change is not None and change > 0:
                    trend = "ADVANCE"
                    advance += 1
                elif change is not None and change < 0:
                    trend = "DECLINE"
                    decline += 1
                else:
                    unchanged += 1

                scripts_data.append(
                    {
                        "symbol": script.symbol,
                        "fyers_symbol": script.fyers_symbol,
                        "captured_at": row.captured_at.isoformat(),
                        "ltp": cls._as_number(row.ltp),
                        "previous_close": cls._as_number(row.previous_close),
                        "change": change,
                        "change_pct": cls._as_number(row.change_pct),
                        "trend": trend,
                    }
                )

            return {
                "captured_at": latest_captured_at.isoformat()
                if latest_captured_at
                else None,
                "advance_count": advance,
                "decline_count": decline,
                "unchanged_count": unchanged,
                "total_scripts": len(active_scripts),
                "scripts": scripts_data,
            }
