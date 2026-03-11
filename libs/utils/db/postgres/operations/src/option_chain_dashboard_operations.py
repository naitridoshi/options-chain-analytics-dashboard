import asyncio
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, desc

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.config.src.fyers import (
    INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
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

log = CustomLogger("DashboardOperations", is_request=False)
logger, listener = log.get_logger()
listener.start()

# Instrument cache with TTL
_instrument_cache: dict[str, Instrument] = {}
_instrument_cache_time: float = 0
INSTRUMENT_CACHE_TTL_SECONDS = 300  # 5 minutes


async def warmup_connection_pool() -> bool:
    """Warm up the database connection pool by establishing connections.

    This should be called during application startup to avoid cold start
    delays on the first request. Also pre-populates the instrument cache.

    Returns:
        bool: True if warmup succeeded, False otherwise
    """
    global _instrument_cache, _instrument_cache_time

    try:
        logger.info("[DB] Warming up connection pool and pre-populating cache...")
        warmup_start = time_module.time()

        async with postgres_connection.get_session() as session:
            # Simple query to establish connection and pre-populate cache
            instrument_repo = get_instruments_repository(session)
            instruments = await instrument_repo.list_ordered(
                where=instrument_repo.model.is_active.is_(True),
                order_by=instrument_repo.model.symbol.asc(),
                limit=10,  # Cache first 10 active instruments
            )

            # Pre-populate cache with all fetched instruments
            if instruments:
                _instrument_cache_time = time_module.time()
                for inst in instruments:
                    _instrument_cache[inst.symbol.upper()] = inst
                    # Also set as default if it's the first one
                    if inst == instruments[0]:
                        _instrument_cache["_default_"] = inst
                logger.info(
                    f"[DB] Pre-populated instrument cache with {len(instruments)} instruments"
                )

        warmup_time = time_module.time() - warmup_start
        logger.info(f"[DB] Connection pool warmup completed in {warmup_time:.3f}s")
        return True

    except Exception as error:
        logger.warning(f"[DB] Connection pool warmup failed: {str(error)}")
        return False


def clear_instrument_cache() -> None:
    """Clear the instrument cache.

    Should be called when instrument data is updated.
    """
    global _instrument_cache, _instrument_cache_time
    _instrument_cache.clear()
    _instrument_cache_time = 0
    logger.info("[DASHBOARD] Instrument cache cleared")


class OptionChainDashboardOperations(BaseOperations[OptionChainIntervalSummary]):
    """Operations for dashboard data retrieval with optimized parallel queries."""

    @staticmethod
    def _as_number(value: Decimal | int | float | None):
        """Convert Decimal to float for JSON serialization."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return float(value)
        return value

    @staticmethod
    def _sum_range(rows: list, start_idx: int, end_idx: int, field_name: str) -> int:
        """Sum values in a range of rows."""
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
        """Calculate Put-Call Ratio."""
        if call_total == 0:
            if put_total == 0:
                return None
            return "INF" if put_total > 0 else "-INF"
        return put_total / call_total

    @classmethod
    def _compute_custom_pcrs(
        cls, strike_rows: list, spot_price: Decimal | None
    ) -> dict:
        """Compute custom PCR metrics for dashboard."""
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
        instrument_id: str,
        today_start_utc: datetime,
    ):
        """Resolve the previous close reference for calculating day change.

        Returns:
            tuple: (previous_close_row, selection_method)
        """
        start_time = time_module.time()

        # Get the most recent snapshot before today
        latest_before_today = await interval_repo.list_ordered(
            where=[
                interval_repo.model.instrument_id == instrument_id,
                interval_repo.model.captured_at < today_start_utc,
            ],
            order_by=desc(interval_repo.model.captured_at),
            limit=1,
        )
        elapsed = time_module.time() - start_time
        logger.debug(f"[DASHBOARD] Previous close query took {elapsed:.3f}s")

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

        # Only query for exact close if it's different from the fallback time
        if fallback_row.captured_at == exact_close_utc:
            return fallback_row, "exact_close_time"

        # Try to get exact close time snapshot
        exact_close_rows = await interval_repo.list_ordered(
            where=[
                interval_repo.model.instrument_id == instrument_id,
                interval_repo.model.captured_at == exact_close_utc,
            ],
            order_by=desc(interval_repo.model.captured_at),
            limit=1,
        )
        elapsed = time_module.time() - start_time
        logger.debug(f"[DASHBOARD] Exact close query took {elapsed:.3f}s")

        if exact_close_rows:
            return exact_close_rows[0], "exact_close_time"
        return fallback_row, "latest_previous_market_day"

    @classmethod
    def _get_cached_instrument(cls, symbol: str | None) -> Instrument | None:
        """Get instrument from cache if valid."""
        global _instrument_cache, _instrument_cache_time

        current_time = time_module.time()
        if current_time - _instrument_cache_time > INSTRUMENT_CACHE_TTL_SECONDS:
            return None  # Cache expired

        cache_key = symbol.upper() if symbol else "_default_"
        return _instrument_cache.get(cache_key)

    @classmethod
    def _set_cached_instrument(cls, symbol: str | None, instrument: Instrument) -> None:
        """Set instrument in cache."""
        global _instrument_cache, _instrument_cache_time

        current_time = time_module.time()
        if current_time - _instrument_cache_time > INSTRUMENT_CACHE_TTL_SECONDS:
            # Reset cache on TTL expiry
            _instrument_cache.clear()

        _instrument_cache_time = current_time
        cache_key = symbol.upper() if symbol else "_default_"
        _instrument_cache[cache_key] = instrument

    @classmethod
    async def _resolve_instrument(
        cls,
        *,
        instrument_repo,
        symbol: str | None,
    ) -> Instrument | None:
        """Resolve instrument by symbol or get default active instrument.

        Uses caching to avoid repeated database queries for instrument data
        which rarely changes.
        """
        # Check cache first
        cached = cls._get_cached_instrument(symbol)
        if cached:
            logger.debug(f"[DASHBOARD] Instrument cache hit - symbol: {symbol}")
            return cached

        # Cache miss - query database
        if symbol:
            instrument = await instrument_repo.get(
                and_(
                    instrument_repo.model.symbol == symbol.upper(),
                    instrument_repo.model.is_active.is_(True),
                )
            )
        else:
            instruments = await instrument_repo.list_ordered(
                where=instrument_repo.model.is_active.is_(True),
                order_by=instrument_repo.model.symbol.asc(),
                limit=1,
            )
            instrument = instruments[0] if instruments else None

        # Update cache
        if instrument:
            cls._set_cached_instrument(symbol, instrument)

        return instrument

    @classmethod
    async def get_dashboard_data(
        cls,
        *,
        symbol: str | None = None,
        market_date: date | None = None,
        timeline_limit: int = 100,
    ) -> dict:
        """Get dashboard data with optimized parallel queries.

        This method runs independent database queries in parallel to minimize
        response time. Typical response time should be < 1 second.

        Args:
            symbol: Trading symbol (e.g., "NIFTY"). If None, uses first active instrument.
            market_date: Date to fetch data for. Defaults to today (IST).
            timeline_limit: Maximum number of timeline entries (not currently used).

        Returns:
            dict: Dashboard data including instrument info, latest snapshot, and strikes.
        """
        total_start = time_module.time()

        # Check cache BEFORE creating session to avoid connection overhead
        cached_instrument = cls._get_cached_instrument(symbol)
        if cached_instrument:
            logger.info(
                f"[DASHBOARD] Instrument cache hit - symbol: {symbol}, skipping DB"
            )

        session_start = time_module.time()
        async with postgres_connection.get_session() as session:
            session_time = time_module.time() - session_start
            if session_time > 1.0:
                logger.warning(f"[DASHBOARD] Session creation took {session_time:.3f}s")

            instrument_repo = get_instruments_repository(session)
            interval_repo = get_option_chain_interval_summaries_repository(session)
            strike_summary_repo = get_option_chain_strike_summaries_repository(session)

            # Step 1: Resolve instrument (must complete first as other queries depend on it)
            step1_start = time_module.time()

            # Use cached instrument if available
            if cached_instrument:
                instrument = cached_instrument
            else:
                instrument = await cls._resolve_instrument(
                    instrument_repo=instrument_repo,
                    symbol=symbol,
                )
            step1_time = time_module.time() - step1_start
            logger.info(f"[DASHBOARD] Step 1 - Resolve instrument: {step1_time:.3f}s")

            if not instrument:
                return {
                    "instrument": None,
                    "market_date": (
                        market_date or datetime.now(IST).date()
                    ).isoformat(),
                    "refresh_seconds": INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS,
                    "timeline": [],
                    "latest": None,
                    "strikes": [],
                }

            # Calculate date range for queries
            resolved_date = market_date or datetime.now(IST).date()
            start_ist = datetime.combine(
                resolved_date,
                time(0, 0, 0, tzinfo=IST),
            )
            end_ist = start_ist + timedelta(days=1)
            start_utc = start_ist.astimezone(timezone.utc)
            end_utc = end_ist.astimezone(timezone.utc)

            # Step 2: Run independent queries in parallel
            # - Get latest interval summary for today
            # - Get previous close reference (for day change calculation)
            step2_start = time_module.time()

            latest_rows_task = interval_repo.list_ordered(
                where=[
                    interval_repo.model.instrument_id == instrument.id,
                    interval_repo.model.captured_at >= start_utc,
                    interval_repo.model.captured_at < end_utc,
                ],
                order_by=desc(interval_repo.model.captured_at),
                limit=1,
            )
            previous_close_task = cls._resolve_previous_close_reference(
                interval_repo=interval_repo,
                instrument_id=instrument.id,
                today_start_utc=start_utc,
            )

            # Wait for both queries to complete in parallel
            (
                latest_rows,
                (previous_close_row, previous_close_selection),
            ) = await asyncio.gather(latest_rows_task, previous_close_task)
            step2_time = time_module.time() - step2_start
            logger.info(f"[DASHBOARD] Step 2 - Parallel queries: {step2_time:.3f}s")

            latest_row = latest_rows[0] if latest_rows else None

            # Step 3: Get strike data (depends on latest_row.snapshot_id)
            step3_start = time_module.time()
            strike_rows = []
            if latest_row:
                strike_rows = await strike_summary_repo.list_ordered(
                    where=strike_summary_repo.model.snapshot_id
                    == latest_row.snapshot_id,
                    order_by=strike_summary_repo.model.strike_price.asc(),
                    limit=100,  # Limit to 100 strikes for performance
                )
            step3_time = time_module.time() - step3_start
            logger.info(f"[DASHBOARD] Step 3 - Strike data: {step3_time:.3f}s")

            # Step 4: Build response
            if latest_row:
                custom_pcrs = cls._compute_custom_pcrs(
                    strike_rows, latest_row.spot_price
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

            total_time = time_module.time() - total_start
            logger.info(f"[DASHBOARD] Total request time: {total_time:.3f}s")

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
                "refresh_seconds": INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS,
                "timeline": [],  # Timeline feature not implemented yet
                "latest": latest,
                "strikes": strikes,
            }
