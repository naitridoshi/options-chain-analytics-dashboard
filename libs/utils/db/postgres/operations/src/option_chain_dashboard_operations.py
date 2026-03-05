from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, desc

from libs.utils.config.src.fyers import SNAPSHOT_INTERVAL_MINUTES
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
                    "refresh_seconds": SNAPSHOT_INTERVAL_MINUTES * 60,
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

            timeline_rows = await interval_repo.list_ordered(
                where=[
                    interval_repo.model.instrument_id == instrument.id,
                    interval_repo.model.captured_at >= start_utc,
                    interval_repo.model.captured_at < end_utc,
                ],
                order_by=desc(interval_repo.model.captured_at),
                limit=max(1, timeline_limit),
            )
            timeline_rows = list(reversed(timeline_rows))

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

            timeline = [
                {
                    "snapshot_id": str(row.snapshot_id),
                    "captured_at": row.captured_at.isoformat(),
                    "spot_price": cls._as_number(row.spot_price),
                    "call_oi_change_sum": row.call_oi_change_sum,
                    "put_oi_change_sum": row.put_oi_change_sum,
                    "net_oi_change_sum": row.net_oi_change_sum,
                    "call_oi_sum": row.call_oi_sum,
                    "put_oi_sum": row.put_oi_sum,
                    "net_oi_sum": row.net_oi_sum,
                    "call_volume_sum": row.call_volume_sum,
                    "put_volume_sum": row.put_volume_sum,
                    "pcr_oi": cls._as_number(row.pcr_oi),
                    "pcr_oi_change": cls._as_number(row.pcr_oi_change),
                    "call_oi_share_pct": cls._as_number(row.call_oi_share_pct),
                    "put_oi_share_pct": cls._as_number(row.put_oi_share_pct),
                    "call_oi_change_share_pct": cls._as_number(
                        row.call_oi_change_share_pct
                    ),
                    "put_oi_change_share_pct": cls._as_number(
                        row.put_oi_change_share_pct
                    ),
                }
                for row in timeline_rows
            ]

            latest = None
            if latest_row:
                latest = {
                    "snapshot_id": str(latest_row.snapshot_id),
                    "captured_at": latest_row.captured_at.isoformat(),
                    "spot_price": cls._as_number(latest_row.spot_price),
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
                    "put_ltp": cls._as_number(row.put_ltp),
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
                "refresh_seconds": SNAPSHOT_INTERVAL_MINUTES * 60,
                "timeline": timeline,
                "latest": latest,
                "strikes": strikes,
            }
