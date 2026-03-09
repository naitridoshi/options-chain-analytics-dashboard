from datetime import datetime
from time import perf_counter
from zoneinfo import ZoneInfo

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from apps.ingestion.platform.modules.option_chain_market_data.src import (
    get_market_data_manager,
)
from apps.ingestion.platform.modules.symbol_refresh.src import (
    SymbolRefreshManager,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.events.src import (
    SymbolListRefreshedEvent,
    get_event_dispatcher,
)
from libs.utils.common.market_state.src import get_market_state_manager

log = CustomLogger("SymbolRefreshScheduler")
logger, listener = log.get_logger()
listener.start()

IST = ZoneInfo("Asia/Kolkata")


class SymbolRefreshScheduler:
    """Scheduler for daily symbol refresh at 8:45 AM IST."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=IST)
        self._started = False
        self.refresh_manager = SymbolRefreshManager()
        self.event_dispatcher = get_event_dispatcher()
        self.market_state = get_market_state_manager()
        self.market_data_manager = get_market_data_manager()

    async def tick(self):
        """Execute symbol refresh job."""
        tick_start = perf_counter()
        tick_status = "unknown"
        now_ist = datetime.now(IST).isoformat()

        logger.info(f"Symbol refresh tick started - now_ist: {now_ist}")

        try:
            # Refresh symbols for all instruments
            refresh_results = (
                await self.refresh_manager.refresh_symbols_for_all_instruments()
            )

            # Detect expiry changes
            expiry_changes = await self.refresh_manager.detect_expiry_changes(
                refresh_results
            )

            if expiry_changes:
                logger.warning(
                    f"Expiry changes detected - "
                    f"changed_instruments: {len(expiry_changes)}"
                )

                # Collect all new symbols
                all_new_symbols = []
                for result in refresh_results.values():
                    if result and result.get("symbols"):
                        all_new_symbols.extend(result["symbols"])

                if all_new_symbols:
                    # Update market state
                    first_result = next(
                        (r for r in refresh_results.values() if r), None
                    )
                    if first_result:
                        symbol_to_strike = {}
                        mapping = first_result.get("symbol_mapping", {})
                        for strike, symbols_dict in mapping.items():
                            for symbol in symbols_dict.values():
                                symbol_to_strike[symbol] = strike
                        self.market_state.update_symbol_mapping(
                            symbol_to_strike, first_result.get("expiry_date")
                        )

                    # Update WebSocket subscriptions
                    await self.market_data_manager.update_symbols(all_new_symbols)

                    # Fire event for any downstream listeners
                    event = SymbolListRefreshedEvent(
                        symbols=all_new_symbols,
                        expiry_date=first_result.get("expiry_date"),
                    )
                    await self.event_dispatcher.dispatch_async(event)

                    tick_status = "expiry_changed"
            else:
                tick_status = "completed"

            logger.info(
                f"Symbol refresh tick completed - "
                f"status: {tick_status}, "
                f"instruments_refreshed: {len(refresh_results)}, "
                f"expiry_changes: {len(expiry_changes)}"
            )

        except Exception as error:
            tick_status = "failed"
            logger.error(f"Symbol refresh tick failed - error: {str(error)}")
            raise

        finally:
            duration_seconds = perf_counter() - tick_start
            logger.info(
                f"Symbol refresh tick duration - "
                f"status: {tick_status}, duration_seconds: {duration_seconds:.3f}"
            )

    def _job_event_listener(self, event):
        """Handle scheduler events."""
        job = self.scheduler.get_job(event.job_id) if event.job_id else None
        next_run_time = (
            job.next_run_time.isoformat()
            if job and getattr(job, "next_run_time", None)
            else None
        )

        if event.code == EVENT_JOB_EXECUTED:
            logger.info(
                f"Symbol refresh job executed - "
                f"job_id: {event.job_id}, "
                f"next_run_time: {next_run_time}"
            )
            return

        if event.code == EVENT_JOB_MISSED:
            logger.warning(
                f"Symbol refresh job missed - "
                f"job_id: {event.job_id}, "
                f"next_run_time: {next_run_time}"
            )
            return

        if event.code == EVENT_JOB_ERROR:
            logger.error(
                f"Symbol refresh job failed - "
                f"job_id: {event.job_id}, "
                f"next_run_time: {next_run_time}, "
                f"exception: {event.exception}"
            )

    async def start(self):
        """Start the symbol refresh scheduler."""
        if self._started:
            return

        try:
            # Schedule job at 8:45 AM IST daily
            job = self.scheduler.add_job(
                self.tick,
                trigger=CronTrigger(
                    hour=8,
                    minute=45,
                    timezone=IST,
                ),
                id="symbol-refresh",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )

            self.scheduler.add_listener(
                self._job_event_listener,
                EVENT_JOB_EXECUTED | EVENT_JOB_MISSED | EVENT_JOB_ERROR,
            )

            self.scheduler.start()
            self._started = True

            logger.info(
                f"Symbol refresh scheduler started - "
                f"job_id: {job.id}, "
                f"next_run_time: {job.next_run_time.isoformat() if job.next_run_time else None}"
            )

        except Exception as error:
            logger.error(
                f"Failed to start symbol refresh scheduler - error: {str(error)}"
            )
            raise

    def stop(self):
        """Stop the symbol refresh scheduler."""
        if not self._started:
            return

        try:
            self.scheduler.shutdown(wait=False)
            self._started = False
            logger.info("Symbol refresh scheduler stopped")
        except Exception as error:
            logger.error(
                f"Error stopping symbol refresh scheduler - error: {str(error)}"
            )


# Global instance
_symbol_refresh_scheduler: SymbolRefreshScheduler | None = None


def get_symbol_refresh_scheduler() -> SymbolRefreshScheduler:
    """Get or create the global symbol refresh scheduler.

    Returns:
        SymbolRefreshScheduler: The global instance
    """
    global _symbol_refresh_scheduler
    if _symbol_refresh_scheduler is None:
        _symbol_refresh_scheduler = SymbolRefreshScheduler()
    return _symbol_refresh_scheduler


symbol_refresh_scheduler = get_symbol_refresh_scheduler()
