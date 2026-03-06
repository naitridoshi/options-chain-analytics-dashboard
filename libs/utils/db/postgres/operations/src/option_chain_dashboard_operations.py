from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, desc

from libs.utils.config.src.fyers import (
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    SNAPSHOT_INTERVAL_SECONDS,
)
from libs.utils.db.postgres.models.src.instrument import Instrument
from libs.utils.db.postgres.models.src.option_chain_interval_summary import (
    OptionChainIntervalSummary,
)
from libs.utils.db.postgres.operations.src.base import BaseOperations
from libs.utils.db.postgres.src.connection import postgres_connection
from libs.utils.db.postgres.src.repository import (
    get_instruments_repository,
    get_option_chain_interval_summaries_repository,
    get_option_chain_strike_summaries_repository,
)

IST = ZoneInfo("Asia/Kolkata")


class OptionChainDashboardOperations(BaseOperations[OptionChainIntervalSummary]):
    @staticmethod
    def _as_number(value: Decimal | int | float | None):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return float(value)
        return value

    @staticmethod
    def _sum_range(rows: list, start_idx: int, end_idx: int, field_name: str) -> int:
        if not rows:
            return 0
        start = max(0, start_idx)
        end = min(len(rows) - 1, end_idx)
        if start > end:
            return 0
        total = 0
        for idx in range(start, end + 1):
            value = getattr(rows[idx], field_name, 0)
            total += int(value or 0)
        return total

    @staticmethod
    def _pcr_value(put_total: int, call_total: int) -> float | str | None:
        if call_total == 0:
            if put_total == 0:
                return None
            return "INF" if put_total > 0 else "-INF"
        return put_total / call_total

    @classmethod
    def _compute_custom_pcrs(
        cls, strike_rows: list, spot_price: Decimal | None
    ) -> dict:
        if not strike_rows or spot_price is None:
            return {
                "coi_pcr_window": None,
                "atm_pcr": None,
                "strength_pcr": None,
            }

        spot_float = float(spot_price)
        atm_index = min(
            range(len(strike_rows)),
            key=lambda idx: abs(float(strike_rows[idx].strike_price) - spot_float),
        )

        call_total_coi_window = cls._sum_range(
            strike_rows, atm_index - 6, atm_index + 6, "call_oi_change"
        )
        put_total_coi_window = cls._sum_range(
            strike_rows, atm_index - 6, atm_index + 6, "put_oi_change"
        )

        call_total_atm = cls._sum_range(
            strike_rows, atm_index - 1, atm_index, "call_oi_change"
        )
        put_total_atm = cls._sum_range(
            strike_rows, atm_index, atm_index + 1, "put_oi_change"
        )

        call_total_strength = cls._sum_range(
            strike_rows, atm_index - 4, atm_index, "call_oi_change"
        )
        put_total_strength = cls._sum_range(
            strike_rows, atm_index, atm_index + 4, "put_oi_change"
        )

        return {
            "coi_pcr_window": cls._pcr_value(
                put_total_coi_window, call_total_coi_window
            ),
            "atm_pcr": cls._pcr_value(put_total_atm, call_total_atm),
            "strength_pcr": cls._pcr_value(put_total_strength, call_total_strength),
        }

    @classmethod
    async def _resolve_previous_close_reference(
        cls,
        *,
        interval_repo,
        instrument_id,
        today_start_utc: datetime,
    ):
        latest_before_today = await interval_repo.list_ordered(
            where=[
                interval_repo.model.instrument_id == instrument_id,
                interval_repo.model.captured_at < today_start_utc,
            ],
            order_by=desc(interval_repo.model.captured_at),
            limit=1,
        )
        if not latest_before_today:
            return None, None

        fallback_row = latest_before_today[0]
        prev_market_date_ist = fallback_row.captured_at.astimezone(IST).date()
        exact_close_ist = datetime.combine(
            prev_market_date_ist,
            time(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0),
            tzinfo=IST,
        )
        exact_close_utc = exact_close_ist.astimezone(timezone.utc)

        exact_close_rows = await interval_repo.list_ordered(
            where=[
                interval_repo.model.instrument_id == instrument_id,
                interval_repo.model.captured_at == exact_close_utc,
            ],
            order_by=desc(interval_repo.model.captured_at),
            limit=1,
        )
        if exact_close_rows:
            return exact_close_rows[0], "exact_close_time"
        return fallback_row, "latest_previous_market_day"

    @classmethod
    async def _resolve_instrument(
        cls,
        *,
        instrument_repo,
        symbol: str | None,
    ) -> Instrument | None:
        if symbol:
            return await instrument_repo.get(
                and_(
                    instrument_repo.model.symbol == symbol.upper(),
                    instrument_repo.model.is_active.is_(True),
                )
            )
        instruments = await instrument_repo.list_ordered(
            where=instrument_repo.model.is_active.is_(True),
            order_by=instrument_repo.model.symbol.asc(),
            limit=1,
        )
        if not instruments:
            return None
        return instruments[0]

    @classmethod
    async def get_dashboard_data(
        cls,
        *,
        symbol: str | None = None,
        market_date: date | None = None,
        timeline_limit: int = 100,
    ) -> dict:
        async with postgres_connection.get_session() as session:
            instrument_repo = get_instruments_repository(session)
            interval_repo = get_option_chain_interval_summaries_repository(session)
            strike_summary_repo = get_option_chain_strike_summaries_repository(session)
            instrument = await cls._resolve_instrument(
                instrument_repo=instrument_repo,
                symbol=symbol,
            )
            if not instrument:
                return {
                    "instrument": None,
                    "market_date": (
                        market_date or datetime.now(IST).date()
                    ).isoformat(),
                    "refresh_seconds": SNAPSHOT_INTERVAL_SECONDS,
                    "timeline": [],
                    "latest": None,
                    "strikes": [],
                }

            resolved_date = market_date or datetime.now(IST).date()
            start_ist = datetime.combine(
                resolved_date,
                time.min,
                tzinfo=IST,
            )
            end_ist = start_ist + timedelta(days=1)
            start_utc = start_ist.astimezone(timezone.utc)
            end_utc = end_ist.astimezone(timezone.utc)

            latest_rows = await interval_repo.list_ordered(
                where=[
                    interval_repo.model.instrument_id == instrument.id,
                    interval_repo.model.captured_at >= start_utc,
                    interval_repo.model.captured_at < end_utc,
                ],
                order_by=desc(interval_repo.model.captured_at),
                limit=1,
            )
            latest_row = latest_rows[0] if latest_rows else None

            strike_rows = []
            if latest_row:
                strike_rows = await strike_summary_repo.list_ordered(
                    where=strike_summary_repo.model.snapshot_id
                    == latest_row.snapshot_id,
                    order_by=strike_summary_repo.model.strike_price.asc(),
                    limit=10000,
                )

            timeline = []

            latest = None
            if latest_row:
                custom_pcrs = cls._compute_custom_pcrs(
                    strike_rows, latest_row.spot_price
                )
                (
                    previous_close_row,
                    previous_close_selection,
                ) = await cls._resolve_previous_close_reference(
                    interval_repo=interval_repo,
                    instrument_id=instrument.id,
                    today_start_utc=start_utc,
                )
                previous_close_spot = (
                    previous_close_row.spot_price if previous_close_row else None
                )
                change_from_prev_close = None
                change_pct_from_prev_close = None
                if previous_close_spot is not None:
                    change_from_prev_close = latest_row.spot_price - previous_close_spot
                    if previous_close_spot != 0:
                        change_pct_from_prev_close = (
                            change_from_prev_close / previous_close_spot
                        ) * Decimal("100")
                latest = {
                    "snapshot_id": str(latest_row.snapshot_id),
                    "captured_at": latest_row.captured_at.isoformat(),
                    "spot_price": cls._as_number(latest_row.spot_price),
                    "prev_close_spot": cls._as_number(previous_close_spot),
                    "prev_close_captured_at": (
                        previous_close_row.captured_at.isoformat()
                        if previous_close_row
                        else None
                    ),
                    "prev_close_selection": previous_close_selection,
                    "change_from_prev_close": cls._as_number(change_from_prev_close),
                    "change_pct_from_prev_close": cls._as_number(
                        change_pct_from_prev_close
                    ),
                    "call_oi_change_sum": latest_row.call_oi_change_sum,
                    "put_oi_change_sum": latest_row.put_oi_change_sum,
                    "net_oi_change_sum": latest_row.net_oi_change_sum,
                    "call_oi_sum": latest_row.call_oi_sum,
                    "put_oi_sum": latest_row.put_oi_sum,
                    "net_oi_sum": latest_row.net_oi_sum,
                    "pcr_oi": cls._as_number(latest_row.pcr_oi),
                    "pcr_oi_change": cls._as_number(latest_row.pcr_oi_change),
                    "call_oi_share_pct": cls._as_number(latest_row.call_oi_share_pct),
                    "put_oi_share_pct": cls._as_number(latest_row.put_oi_share_pct),
                    "call_oi_change_share_pct": cls._as_number(
                        latest_row.call_oi_change_share_pct
                    ),
                    "put_oi_change_share_pct": cls._as_number(
                        latest_row.put_oi_change_share_pct
                    ),
                    "coi_pcr_window": custom_pcrs["coi_pcr_window"],
                    "atm_pcr": custom_pcrs["atm_pcr"],
                    "strength_pcr": custom_pcrs["strength_pcr"],
                }

            strikes = [
                {
                    "strike_price": cls._as_number(row.strike_price),
                    "call_oi_change": row.call_oi_change,
                    "put_oi_change": row.put_oi_change,
                    "net_oi_change": row.net_oi_change,
                    "call_oi": row.call_oi,
                    "put_oi": row.put_oi,
                    "net_oi": row.net_oi,
                    "call_volume": row.call_volume,
                    "put_volume": row.put_volume,
                    "call_ltp": cls._as_number(row.call_ltp),
                    "call_ltp_change": cls._as_number(row.call_ltp_change),
                    "put_ltp": cls._as_number(row.put_ltp),
                    "put_ltp_change": cls._as_number(row.put_ltp_change),
                }
                for row in strike_rows
            ]

            return {
                "instrument": {
                    "id": str(instrument.id),
                    "symbol": instrument.symbol,
                    "name": getattr(instrument, "name", None) or instrument.symbol,
                    "exchange": getattr(instrument, "exchange", None),
                    "instrument_type": getattr(instrument, "instrument_type", None),
                    "fyers_symbol": instrument.fyers_symbol,
                },
                "market_date": resolved_date.isoformat(),
                "refresh_seconds": SNAPSHOT_INTERVAL_SECONDS,
                "timeline": timeline,
                "latest": latest,
                "strikes": strikes,
            }
