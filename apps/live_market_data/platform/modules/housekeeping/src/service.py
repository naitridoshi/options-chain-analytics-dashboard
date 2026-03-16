from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone

from libs.platform.modules.option_chain_snapshot.src import (
    IST,
    is_market_open_now,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.common.runtime_store.src import RuntimeSnapshotService
from libs.utils.config.src.fyers import MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE
from libs.utils.config.src.redis import (
    REDIS_MARKET_CLOSE_FINALIZE_DELAY_SECONDS,
    REDIS_ROLLOVER_CHECK_INTERVAL_SECONDS,
)
from libs.utils.db.redis.src import (
    RedisOptionChainSnapshotStore,
    RedisRolloverStore,
)

log = CustomLogger("LiveMarketHousekeepingService")
logger, listener = log.get_logger()
listener.start()


class LiveMarketHousekeepingService:
    """Handles day rollover and retention for Redis runtime state."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Live market housekeeping service started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Live market housekeeping service stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._run_housekeeping()
                await asyncio.sleep(REDIS_ROLLOVER_CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error(f"Housekeeping loop failed - error: {str(error)}")
                await asyncio.sleep(5)

    async def _run_housekeeping(self) -> None:
        now_utc = datetime.now(timezone.utc)
        trade_date = now_utc.astimezone(IST).date().isoformat()

        if await self._should_finalize_today(now_utc):
            await self._finalize_trade_date(trade_date)

        await self._cleanup_old_trade_dates(current_trade_date=trade_date)

    async def _should_finalize_today(self, now_utc: datetime) -> bool:
        if is_market_open_now(now_utc):
            return False
        now_ist = now_utc.astimezone(IST)
        finalize_after = datetime.combine(
            now_ist.date(),
            time(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE),
            tzinfo=IST,
        ) + timedelta(seconds=REDIS_MARKET_CLOSE_FINALIZE_DELAY_SECONDS)
        return now_ist >= finalize_after

    async def _finalize_trade_date(self, trade_date: str) -> None:
        if await RedisRolloverStore.is_marker_set("finalized", trade_date):
            return

        instruments = InstrumentCatalogService.get_active_instruments()
        for instrument in instruments:
            final_snapshot, selection = await self._resolve_final_snapshot(
                instrument_symbol=instrument.symbol,
                trade_date=trade_date,
            )
            if not final_snapshot:
                continue
            latest = final_snapshot.get("latest") or {}
            captured_at = latest.get("captured_at")
            strikes = final_snapshot.get("strikes") or []
            spot_price = latest.get("spot_price")
            if not captured_at or spot_price is None or not strikes:
                continue
            await RuntimeSnapshotService.save_previous_day_final_snapshot(
                instrument=instrument,
                captured_at=datetime.fromisoformat(captured_at),
                spot_price=spot_price,
                strike_rows=_inflate_runtime_strikes(strikes),
                selection=selection,
            )

        await RedisRolloverStore.set_marker("finalized", trade_date)
        logger.info(
            f"Finalized previous-day snapshot retention - trade_date: {trade_date}"
        )

    async def _resolve_final_snapshot(
        self,
        *,
        instrument_symbol: str,
        trade_date: str,
    ) -> tuple[dict | None, str]:
        exact_close_utc = datetime.combine(
            date.fromisoformat(trade_date),
            time(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE),
            tzinfo=IST,
        ).astimezone(timezone.utc)
        exact_close_snapshot = await RedisOptionChainSnapshotStore.get_snapshot(
            instrument_symbol=instrument_symbol,
            trade_date=trade_date,
            interval_ts=exact_close_utc.isoformat(),
        )
        if exact_close_snapshot:
            return exact_close_snapshot, "exact_close_time"

        latest_snapshot = await RedisOptionChainSnapshotStore.get_latest_snapshot(
            instrument_symbol=instrument_symbol,
            trade_date=trade_date,
        )
        if latest_snapshot:
            return latest_snapshot, "latest_previous_market_day"

        return None, "latest_previous_market_day"

    async def _cleanup_old_trade_dates(self, *, current_trade_date: str) -> None:
        if await RedisRolloverStore.is_marker_set("cleanup", current_trade_date):
            return

        instruments = InstrumentCatalogService.get_active_instruments()
        for instrument in instruments:
            trade_dates = await RedisOptionChainSnapshotStore.list_trade_dates(
                instrument.symbol
            )
            for trade_date in trade_dates:
                if trade_date >= current_trade_date:
                    continue
                await self._finalize_trade_date(trade_date)
                await RedisOptionChainSnapshotStore.delete_trade_date(
                    instrument_symbol=instrument.symbol,
                    trade_date=trade_date,
                )

        await RedisRolloverStore.set_marker("cleanup", current_trade_date)
        logger.info(
            f"Cleaned up old intraday trade dates before - trade_date: {current_trade_date}"
        )


def _inflate_runtime_strikes(strikes: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for item in strikes:
        strike_price = item.get("strike_price")
        rows.append(
            {
                "strike_price": strike_price,
                "option_type": "CE",
                "option_contract_id": item.get("call_option_contract_id"),
                "oi_change": item.get("call_oi_change"),
                "open_interest": item.get("call_oi"),
                "volume": item.get("call_volume"),
                "ltp": item.get("call_ltp"),
                "ltp_change": item.get("call_ltp_change"),
            }
        )
        rows.append(
            {
                "strike_price": strike_price,
                "option_type": "PE",
                "option_contract_id": item.get("put_option_contract_id"),
                "oi_change": item.get("put_oi_change"),
                "open_interest": item.get("put_oi"),
                "volume": item.get("put_volume"),
                "ltp": item.get("put_ltp"),
                "ltp_change": item.get("put_ltp_change"),
            }
        )
    return rows
