from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import and_, desc, select

BASE_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, BASE_DIR)

from libs.utils.config.src.fyers import SNAPSHOT_INTERVAL_SECONDS  # noqa: E402
from libs.utils.db.postgres.models.src.instrument import (  # noqa: E402
    Instrument,
)
from libs.utils.db.postgres.models.src.option_chain_interval_summary import (  # noqa: E402
    OptionChainIntervalSummary,
)
from libs.utils.db.postgres.models.src.option_chain_strike_summary import (  # noqa: E402
    OptionChainStrikeSummary,
)
from libs.utils.db.postgres.src.connection import (  # noqa: E402
    postgres_connection,
)
from libs.utils.db.redis.src import (  # noqa: E402
    RedisLiveMarketStore,
    RedisOptionChainSnapshotStore,
    previous_day_final_snapshot_key,
    redis_client_manager,
)

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class HistoricalMarketDay:
    instrument: Instrument
    snapshots: list[OptionChainIntervalSummary]
    strike_rows_by_snapshot_id: dict
    previous_day_final: OptionChainIntervalSummary | None
    previous_day_final_strikes: list[OptionChainStrikeSummary]


async def main() -> None:
    args = _parse_args()
    market_day = await _load_historical_market_day(
        symbol=args.symbol,
        historical_date=date.fromisoformat(args.historical_date),
    )

    seeded = await _seed_redis_runtime_state(
        market_day=market_day,
        clear_today_first=args.clear_today_first,
        seed_previous_day_final=not args.skip_previous_day_final,
    )
    print(
        "Seeded Redis runtime snapshots",
        f"symbol={market_day.instrument.symbol}",
        f"historical_date={args.historical_date}",
        f"target_trade_date={seeded['target_trade_date']}",
        f"snapshots={seeded['snapshots_seeded']}",
    )

    if args.stream_live:
        await _stream_synthetic_live_updates(
            market_day=market_day,
            cycles=args.publish_cycles,
            interval_seconds=args.publish_interval_seconds,
            max_live_strikes=args.max_live_strikes,
        )

    await redis_client_manager.close()
    await postgres_connection.close_engine()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay one historical Postgres market day into Redis using today's trade "
            "date, and optionally publish synthetic live websocket updates."
        )
    )
    parser.add_argument("--symbol", required=True, help="Instrument symbol, e.g. NIFTY")
    parser.add_argument(
        "--historical-date",
        required=True,
        help="Historical market date in YYYY-MM-DD format to replay from Postgres",
    )
    parser.add_argument(
        "--clear-today-first",
        action="store_true",
        help="Delete today's Redis runtime snapshot keys for this instrument before seeding",
    )
    parser.add_argument(
        "--skip-previous-day-final",
        action="store_true",
        help="Do not seed the retained previous-day final snapshot in Redis",
    )
    parser.add_argument(
        "--stream-live",
        action="store_true",
        help="After seeding, publish synthetic live ltp/avg_price updates into Redis",
    )
    parser.add_argument(
        "--publish-cycles",
        type=int,
        default=20,
        help="Number of live publish cycles to emit when --stream-live is enabled",
    )
    parser.add_argument(
        "--publish-interval-seconds",
        type=float,
        default=1.0,
        help="Delay between live publish cycles when --stream-live is enabled",
    )
    parser.add_argument(
        "--max-live-strikes",
        type=int,
        default=12,
        help="Maximum number of strikes nearest spot to include in synthetic live streaming",
    )
    return parser.parse_args()


async def _load_historical_market_day(
    *,
    symbol: str,
    historical_date: date,
) -> HistoricalMarketDay:
    async with postgres_connection.get_session() as session:
        instrument = await session.scalar(
            select(Instrument).where(
                and_(
                    Instrument.symbol == symbol.upper(),
                    Instrument.is_active.is_(True),
                )
            )
        )
        if instrument is None:
            raise ValueError(f"Active instrument not found for symbol={symbol}")

        start_utc, end_utc = _market_day_bounds_utc(historical_date)
        snapshots = list(
            (
                await session.scalars(
                    select(OptionChainIntervalSummary)
                    .where(
                        and_(
                            OptionChainIntervalSummary.instrument_id == instrument.id,
                            OptionChainIntervalSummary.captured_at >= start_utc,
                            OptionChainIntervalSummary.captured_at < end_utc,
                        )
                    )
                    .order_by(OptionChainIntervalSummary.captured_at.asc())
                )
            ).all()
        )
        if not snapshots:
            raise ValueError(
                f"No option-chain interval summaries found for symbol={symbol} on {historical_date.isoformat()}"
            )

        snapshot_ids = [row.snapshot_id for row in snapshots]
        strike_rows = list(
            (
                await session.scalars(
                    select(OptionChainStrikeSummary)
                    .where(OptionChainStrikeSummary.snapshot_id.in_(snapshot_ids))
                    .order_by(
                        OptionChainStrikeSummary.captured_at.asc(),
                        OptionChainStrikeSummary.strike_price.asc(),
                    )
                )
            ).all()
        )

        strike_rows_by_snapshot_id: dict = defaultdict(list)
        for row in strike_rows:
            strike_rows_by_snapshot_id[row.snapshot_id].append(row)

        previous_day_final = await session.scalar(
            select(OptionChainIntervalSummary)
            .where(
                and_(
                    OptionChainIntervalSummary.instrument_id == instrument.id,
                    OptionChainIntervalSummary.captured_at < start_utc,
                )
            )
            .order_by(desc(OptionChainIntervalSummary.captured_at))
            .limit(1)
        )

        previous_day_final_strikes: list[OptionChainStrikeSummary] = []
        if previous_day_final is not None:
            previous_day_final_strikes = list(
                (
                    await session.scalars(
                        select(OptionChainStrikeSummary)
                        .where(
                            OptionChainStrikeSummary.snapshot_id
                            == previous_day_final.snapshot_id
                        )
                        .order_by(OptionChainStrikeSummary.strike_price.asc())
                    )
                ).all()
            )

        return HistoricalMarketDay(
            instrument=instrument,
            snapshots=snapshots,
            strike_rows_by_snapshot_id=strike_rows_by_snapshot_id,
            previous_day_final=previous_day_final,
            previous_day_final_strikes=previous_day_final_strikes,
        )


async def _seed_redis_runtime_state(
    *,
    market_day: HistoricalMarketDay,
    clear_today_first: bool,
    seed_previous_day_final: bool,
) -> dict:
    target_trade_date = datetime.now(IST).date()
    target_trade_date_iso = target_trade_date.isoformat()

    if clear_today_first:
        await RedisOptionChainSnapshotStore.delete_trade_date(
            instrument_symbol=market_day.instrument.symbol,
            trade_date=target_trade_date_iso,
        )

    for snapshot in market_day.snapshots:
        payload = _build_runtime_payload(
            instrument=market_day.instrument,
            summary_row=snapshot,
            strike_rows=market_day.strike_rows_by_snapshot_id[snapshot.snapshot_id],
            remapped_market_date=target_trade_date,
        )
        await RedisOptionChainSnapshotStore.save_intraday_snapshot(
            instrument_symbol=market_day.instrument.symbol,
            trade_date=target_trade_date_iso,
            interval_ts=payload["latest"]["captured_at"],
            payload=payload,
        )

    if seed_previous_day_final and market_day.previous_day_final is not None:
        payload = _build_runtime_payload(
            instrument=market_day.instrument,
            summary_row=market_day.previous_day_final,
            strike_rows=market_day.previous_day_final_strikes,
            remapped_market_date=target_trade_date - timedelta(days=1),
        )
        client = await redis_client_manager.get_client()
        await client.delete(
            previous_day_final_snapshot_key(market_day.instrument.symbol)
        )
        await RedisOptionChainSnapshotStore.save_previous_day_final_snapshot(
            instrument_symbol=market_day.instrument.symbol,
            payload=payload,
        )

    return {
        "target_trade_date": target_trade_date_iso,
        "snapshots_seeded": len(market_day.snapshots),
    }


async def _stream_synthetic_live_updates(
    *,
    market_day: HistoricalMarketDay,
    cycles: int,
    interval_seconds: float,
    max_live_strikes: int,
) -> None:
    latest_snapshot = market_day.snapshots[-1]
    strike_rows = market_day.strike_rows_by_snapshot_id[latest_snapshot.snapshot_id]
    if not strike_rows:
        raise ValueError(
            "Cannot stream live updates because the latest snapshot has no strike rows"
        )

    selected_rows = _select_live_strikes(
        strike_rows=strike_rows,
        spot_price=latest_snapshot.spot_price,
        max_live_strikes=max_live_strikes,
    )
    print(
        "Publishing synthetic live updates",
        f"symbol={market_day.instrument.symbol}",
        f"cycles={cycles}",
        f"strikes={len(selected_rows)}",
        f"interval_seconds={interval_seconds}",
    )

    for cycle in range(cycles):
        now_iso = datetime.now(timezone.utc).isoformat()
        for index, row in enumerate(selected_rows):
            call_ltp = _float_or_none(row.call_ltp)
            put_ltp = _float_or_none(row.put_ltp)

            if call_ltp is not None:
                call_payload = _build_live_payload(
                    instrument_symbol=market_day.instrument.symbol,
                    strike_price=row.strike_price,
                    option_type="CE",
                    base_ltp=call_ltp,
                    cycle=cycle,
                    index=index,
                    last_update=now_iso,
                )
                await RedisLiveMarketStore.write_live_symbol(
                    instrument_symbol=market_day.instrument.symbol,
                    symbol=call_payload["symbol"],
                    payload=call_payload,
                )

            if put_ltp is not None:
                put_payload = _build_live_payload(
                    instrument_symbol=market_day.instrument.symbol,
                    strike_price=row.strike_price,
                    option_type="PE",
                    base_ltp=put_ltp,
                    cycle=cycle,
                    index=index,
                    last_update=now_iso,
                )
                await RedisLiveMarketStore.write_live_symbol(
                    instrument_symbol=market_day.instrument.symbol,
                    symbol=put_payload["symbol"],
                    payload=put_payload,
                )

        await asyncio.sleep(interval_seconds)


def _market_day_bounds_utc(market_date: date) -> tuple[datetime, datetime]:
    start_ist = datetime.combine(market_date, time.min, tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


def _remap_timestamp_to_trade_date(
    *,
    source_timestamp: datetime,
    target_trade_date: date,
) -> datetime:
    source_ist = source_timestamp.astimezone(IST)
    target_ist = datetime.combine(
        target_trade_date,
        source_ist.timetz().replace(tzinfo=IST),
        tzinfo=IST,
    )
    return target_ist.astimezone(timezone.utc)


def _build_runtime_payload(
    *,
    instrument: Instrument,
    summary_row: OptionChainIntervalSummary,
    strike_rows: list[OptionChainStrikeSummary],
    remapped_market_date: date,
) -> dict:
    remapped_captured_at = _remap_timestamp_to_trade_date(
        source_timestamp=summary_row.captured_at,
        target_trade_date=remapped_market_date,
    )
    custom_pcrs = _compute_custom_pcrs(strike_rows, summary_row.spot_price)

    return {
        "instrument": {
            "id": str(instrument.id),
            "symbol": instrument.symbol,
            "name": getattr(instrument, "name", None) or instrument.symbol,
            "exchange": getattr(instrument, "exchange", None),
            "instrument_type": getattr(instrument, "instrument_type", None),
            "fyers_symbol": instrument.fyers_symbol,
        },
        "market_date": remapped_market_date.isoformat(),
        "refresh_seconds": SNAPSHOT_INTERVAL_SECONDS,
        "latest": {
            "snapshot_id": str(summary_row.snapshot_id),
            "captured_at": remapped_captured_at.isoformat(),
            "spot_price": _float_or_none(summary_row.spot_price),
            "call_oi_change_sum": summary_row.call_oi_change_sum,
            "put_oi_change_sum": summary_row.put_oi_change_sum,
            "net_oi_change_sum": summary_row.net_oi_change_sum,
            "call_oi_sum": summary_row.call_oi_sum,
            "put_oi_sum": summary_row.put_oi_sum,
            "net_oi_sum": summary_row.net_oi_sum,
            "pcr_oi": _float_or_none(summary_row.pcr_oi),
            "pcr_oi_change": _float_or_none(summary_row.pcr_oi_change),
            "call_oi_share_pct": _float_or_none(summary_row.call_oi_share_pct),
            "put_oi_share_pct": _float_or_none(summary_row.put_oi_share_pct),
            "call_oi_change_share_pct": _float_or_none(
                summary_row.call_oi_change_share_pct
            ),
            "put_oi_change_share_pct": _float_or_none(
                summary_row.put_oi_change_share_pct
            ),
            "coi_pcr_window": custom_pcrs["coi_pcr_window"],
            "atm_pcr": custom_pcrs["atm_pcr"],
            "strength_pcr": custom_pcrs["strength_pcr"],
        },
        "strikes": [
            {
                "strike_price": _float_or_none(row.strike_price),
                "call_oi_change": row.call_oi_change,
                "put_oi_change": row.put_oi_change,
                "net_oi_change": row.net_oi_change,
                "call_oi": row.call_oi,
                "put_oi": row.put_oi,
                "net_oi": row.net_oi,
                "call_volume": row.call_volume,
                "put_volume": row.put_volume,
                "call_ltp": _float_or_none(row.call_ltp),
                "call_ltp_change": _float_or_none(row.call_ltp_change),
                "put_ltp": _float_or_none(row.put_ltp),
                "put_ltp_change": _float_or_none(row.put_ltp_change),
            }
            for row in strike_rows
        ],
    }


def _compute_custom_pcrs(
    strike_rows: list[OptionChainStrikeSummary],
    spot_price: Decimal | None,
) -> dict:
    """
    Compute custom PCR metrics based on spot price and strike positions.

    PCR Formulas (consistent with snapshot_service.py):
    - COI PCR Window: Sum of PUT OI changes in 6-strike window / Sum of CALL OI changes in 6-strike window
    - ATM PCR: (PUT COI at ATM + PUT COI at 1 strike BELOW) / (CALL COI at ATM + CALL COI at 1 strike ABOVE)
      Example: If ATM = 23200,
        PUT: 23200 + 23150 (indices N-1 to N)
        CALL: 23200 + 23250 (indices N to N+1)
    - Strength PCR: PUT COI of (ATM + 4 strikes BELOW) / CALL COI of (ATM + 4 strikes ABOVE)
      Example: If ATM = 24600,
        CALL: 24600, 24650, 24700, 24750, 24800 (indices N to N+4)
        PUT: 24600, 24550, 24500, 24450, 24400 (indices N-4 to N)

    Note: Strikes must be sorted by strike_price ascending (lower strikes = lower indices).
    """
    if not strike_rows or spot_price is None:
        return {
            "coi_pcr_window": None,
            "atm_pcr": None,
            "strength_pcr": None,
        }

    atm_index = min(
        range(len(strike_rows)),
        key=lambda idx: abs(float(strike_rows[idx].strike_price) - float(spot_price)),
    )
    return {
        # COI PCR Window: 6 strikes on each side of ATM
        "coi_pcr_window": _pcr(
            _sum_range(strike_rows, atm_index - 6, atm_index + 6, "put_oi_change"),
            _sum_range(strike_rows, atm_index - 6, atm_index + 6, "call_oi_change"),
        ),
        # ATM PCR: CALL at ATM + 1 strike ABOVE, PUT at ATM + 1 strike BELOW
        # Higher index = higher strike (sorted ascending)
        "atm_pcr": _pcr(
            _sum_range(strike_rows, atm_index - 1, atm_index, "put_oi_change"),
            _sum_range(strike_rows, atm_index, atm_index + 1, "call_oi_change"),
        ),
        # Strength PCR: CALL at ATM and 4 strikes ABOVE, PUT at ATM and 4 strikes BELOW
        "strength_pcr": _pcr(
            _sum_range(strike_rows, atm_index - 4, atm_index, "put_oi_change"),
            _sum_range(strike_rows, atm_index, atm_index + 4, "call_oi_change"),
        ),
    }


def _sum_range(
    rows: list[OptionChainStrikeSummary],
    start_idx: int,
    end_idx: int,
    field_name: str,
) -> int:
    start = max(0, start_idx)
    end = min(len(rows) - 1, end_idx)
    if start > end:
        return 0
    total = 0
    for idx in range(start, end + 1):
        total += int(getattr(rows[idx], field_name) or 0)
    return total


def _pcr(put_total: int, call_total: int):
    if call_total == 0:
        if put_total == 0:
            return None
        return "INF" if put_total > 0 else "-INF"
    return put_total / call_total


def _select_live_strikes(
    *,
    strike_rows: list[OptionChainStrikeSummary],
    spot_price: Decimal,
    max_live_strikes: int,
) -> list[OptionChainStrikeSummary]:
    sorted_rows = sorted(
        strike_rows,
        key=lambda row: abs(float(row.strike_price) - float(spot_price)),
    )
    return sorted(
        sorted_rows[: max(1, max_live_strikes)],
        key=lambda row: row.strike_price,
    )


def _build_live_payload(
    *,
    instrument_symbol: str,
    strike_price: Decimal,
    option_type: str,
    base_ltp: float,
    cycle: int,
    index: int,
    last_update: str,
) -> dict:
    wave = math.sin(cycle + index / 3)
    ltp = round(base_ltp + wave * max(0.5, base_ltp * 0.005), 2)
    avg_price = round(base_ltp + wave * max(0.25, base_ltp * 0.003), 2)
    strike_key = str(int(float(strike_price)))
    return {
        "instrument_symbol": instrument_symbol,
        "symbol": f"REPLAY:{instrument_symbol}:{strike_key}:{option_type}",
        "strike_price": strike_key,
        "option_type": option_type,
        "ltp": ltp,
        "avg_price": avg_price,
        "last_update": last_update,
        "stale_after_seconds": 15,
    }


def _float_or_none(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


if __name__ == "__main__":
    asyncio.run(main())
