from datetime import datetime, timedelta, timezone
from time import perf_counter

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from apps.fastapi.platform.modules.option_chain_snapshot.src.service import (
    OptionChainSnapshotService,
)
from libs.platform.modules.option_chain_snapshot.src import (
    IST,
    is_market_open_now,
)
from libs.utils.common.constants.src.custom_logger import Colors
from libs.utils.common.custom_logger.src import CustomLogger, color_string
from libs.utils.common.enums.src.custom_logger import LogType
from libs.utils.config.src.fyers import (
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    SNAPSHOT_INTERVAL_SECONDS,
)
from libs.utils.db.postgres.operations.src import OptionSnapshotOperations

log = CustomLogger("OptionChainSnapshotScheduler")
logger, listener = log.get_logger()
listener.start()


class OptionChainSnapshotScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=IST)
        self._started = False

    async def tick(self):
        tick_start = perf_counter()
        tick_status = "unknown"
        now = datetime.now(timezone.utc)
        now_utc = now.isoformat()
        now_ist = now.astimezone(IST).isoformat()
        logger.info(
            "Scheduler tick fired - "
            f"now_utc: {now_utc}, interval_seconds: {SNAPSHOT_INTERVAL_SECONDS}"
        )
        try:
            if not is_market_open_now():
                tick_status = "market_closed"
                logger.info(
                    "Scheduler tick skipped - market closed at this time - "
                    f"now_utc: {now_utc}, "
                    f"now_ist: {now_ist}, "
                    f"market_window_ist: {MARKET_OPEN_HOUR:02d}:{MARKET_OPEN_MINUTE:02d}-"
                    f"{MARKET_CLOSE_HOUR:02d}:{MARKET_CLOSE_MINUTE:02d}",
                )
                return
            result = (
                await OptionChainSnapshotService.capture_for_all_active_instruments()
            )
            tick_status = "completed"
            logger.info(
                "Scheduler tick completed - "
                f"now_utc: {now_utc}, processed_instruments: {result.get('processed_instruments')}, "
                f"snapshots_created: {result.get('snapshots_created')}, "
                f"strikes_inserted: {result.get('strikes_inserted')}"
            )
        except Exception:
            tick_status = "failed"
            raise
        finally:
            duration_seconds = perf_counter() - tick_start
            logger.info(
                "Scheduler tick duration - "
                f"status: {tick_status}, duration_seconds: {duration_seconds:.3f}"
            )

    def _job_event_listener(self, event):
        job = self.scheduler.get_job(event.job_id) if event.job_id else None
        next_run_time = (
            job.next_run_time.isoformat()
            if job and getattr(job, "next_run_time", None)
            else None
        )
        if event.code == EVENT_JOB_EXECUTED:
            logger.info(
                "Scheduler job executed - "
                f"job_id: {event.job_id}, "
                f"scheduled_run_time: {event.scheduled_run_time.isoformat() if event.scheduled_run_time else None}, "
                f"next_run_time: {next_run_time}"
            )
            return
        if event.code == EVENT_JOB_MISSED:
            logger.warning(
                "Scheduler job missed - "
                f"job_id: {event.job_id}, scheduled_run_time: {event.scheduled_run_time.isoformat() if event.scheduled_run_time else None}, "
                f"next_run_time: {next_run_time}"
            )
            return
        if event.code == EVENT_JOB_ERROR:
            logger.error(
                "Scheduler job failed - "
                f"job_id: {event.job_id}, scheduled_run_time: {event.scheduled_run_time.isoformat() if event.scheduled_run_time else None}, "
                f"next_run_time: {next_run_time}, "
                f"exception: {event.exception}"
            )

    async def start(self):
        if self._started:
            return
        now_ist = datetime.now(IST)
        latest_captured_at = (
            await OptionSnapshotOperations.get_latest_captured_at_for_today_ist()
        )
        startup_reason = "no_snapshot_today"
        candidate_start_ist = now_ist
        run_immediately_on_startup = False
        if latest_captured_at is not None:
            candidate_start_ist = latest_captured_at.astimezone(IST) + timedelta(
                seconds=SNAPSHOT_INTERVAL_SECONDS
            )
            startup_reason = "last_plus_interval"

        if candidate_start_ist > now_ist:
            start_date_ist = candidate_start_ist
        else:
            if latest_captured_at is not None:
                startup_reason = "candidate_in_past_use_now"
                run_immediately_on_startup = True
            start_date_ist = now_ist

        job = self.scheduler.add_job(
            self.tick,
            trigger=IntervalTrigger(
                seconds=SNAPSHOT_INTERVAL_SECONDS,
                start_date=start_date_ist,
            ),
            id="options-chain-snapshot",
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
            "Scheduler startup decision - "
            f"reason: {startup_reason}, "
            f"latest_captured_at_ist: {latest_captured_at.astimezone(IST).isoformat() if latest_captured_at else None}, "
            f"candidate_start_time_ist: {candidate_start_ist.isoformat()}, "
            f"selected_start_time_ist: {start_date_ist.isoformat()}",
            extra={"logType": LogType.STARTUP.value},
        )
        logger.info(
            color_string(
                "Option chain snapshot scheduler started - "
                f"job_id: {job.id}, interval_seconds: {SNAPSHOT_INTERVAL_SECONDS}, "
                f"latest_captured_at_ist: {latest_captured_at.astimezone(IST).isoformat() if latest_captured_at else None}, "
                f"computed_start_time_ist: {start_date_ist.isoformat()}, "
                f"next_run_time: {job.next_run_time.isoformat() if job.next_run_time else None}",
                Colors.BOLD_RED,
            ),
            extra={"logType": LogType.STARTUP.value},
        )
        if run_immediately_on_startup:
            logger.info(
                "Running immediate startup snapshot because computed candidate time was already in the past."
            )
            await self.tick()

    def stop(self):
        if not self._started:
            return
        self.scheduler.shutdown(wait=False)
        self._started = False
        logger.info("Option chain snapshot scheduler stopped")


snapshot_scheduler = OptionChainSnapshotScheduler()
