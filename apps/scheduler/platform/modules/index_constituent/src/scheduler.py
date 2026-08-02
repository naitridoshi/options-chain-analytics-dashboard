from datetime import datetime, timedelta
from time import perf_counter

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from apps.fastapi.platform.modules.index_snapshot.src.constituent_service import (
    ConstituentSnapshotService,
)
from libs.platform.modules.option_chain_snapshot.src import (
    IST,
    is_market_open_now,
)
from libs.utils.common.constants.src.custom_logger import Colors
from libs.utils.common.custom_logger.src import CustomLogger, color_string
from libs.utils.common.enums.src.custom_logger import LogType
from libs.utils.common.runtime_store.src import RuntimeConstituentService
from libs.utils.config.src.fyers import (
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
)

log = CustomLogger("ConstituentSnapshotScheduler")
logger, listener = log.get_logger()
listener.start()


class ConstituentSnapshotScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=IST)
        self._started = False

    async def tick(self):
        tick_start = perf_counter()
        tick_status = "unknown"
        now = datetime.now(IST)
        now_ist = now.isoformat()
        logger.info(
            "Constituent scheduler tick fired - "
            f"now_ist: {now_ist}, interval_seconds: {SCRIPTS_SNAPSHOT_INTERVAL_SECONDS}"
        )
        try:
            if not is_market_open_now():
                tick_status = "market_closed"
                logger.info(
                    "Constituent scheduler tick skipped - market closed - "
                    f"now_ist: {now_ist}, "
                    f"market_window_ist: {MARKET_OPEN_HOUR:02d}:{MARKET_OPEN_MINUTE:02d}-"
                    f"{MARKET_CLOSE_HOUR:02d}:{MARKET_CLOSE_MINUTE:02d}",
                )
                return
            result = await ConstituentSnapshotService.capture_for_all_constituents()
            tick_status = "completed"
            logger.info(
                "Constituent scheduler tick completed - "
                f"processed: {result.get('processed_constituents')}, "
                f"snapshots: {result.get('snapshots_created')}"
            )
        except Exception:
            tick_status = "failed"
            raise
        finally:
            duration_seconds = perf_counter() - tick_start
            logger.info(
                "Constituent scheduler tick duration - "
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
                "Constituent scheduler job executed - "
                f"job_id: {event.job_id}, next_run_time: {next_run_time}"
            )
            return
        if event.code == EVENT_JOB_MISSED:
            logger.warning(
                "Constituent scheduler job missed - "
                f"job_id: {event.job_id}, next_run_time: {next_run_time}"
            )
            return
        if event.code == EVENT_JOB_ERROR:
            logger.error(
                "Constituent scheduler job failed - "
                f"job_id: {event.job_id}, next_run_time: {next_run_time}, "
                f"exception: {event.exception}"
            )

    async def start(self):
        if self._started:
            return
        now_ist = datetime.now(IST)
        latest_captured_at = (
            await RuntimeConstituentService.get_latest_captured_at_for_today_ist()
        )
        candidate_start_ist = now_ist
        run_immediately_on_startup = False
        if latest_captured_at is not None:
            candidate_start_ist = latest_captured_at.astimezone(IST) + timedelta(
                seconds=SCRIPTS_SNAPSHOT_INTERVAL_SECONDS
            )

        if candidate_start_ist > now_ist:
            start_date_ist = candidate_start_ist
        else:
            run_immediately_on_startup = True
            start_date_ist = now_ist

        job = self.scheduler.add_job(
            self.tick,
            trigger=IntervalTrigger(
                seconds=SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
                start_date=start_date_ist,
            ),
            id="constituents-snapshot",
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
            color_string(
                "Constituent snapshot scheduler started - "
                f"job_id: {job.id}, interval_seconds: {SCRIPTS_SNAPSHOT_INTERVAL_SECONDS}, "
                f"next_run_time: {job.next_run_time.isoformat() if job.next_run_time else None}",
                Colors.BOLD_RED,
            ),
            extra={"logType": LogType.STARTUP.value},
        )
        if run_immediately_on_startup:
            logger.info("Running immediate startup constituent snapshot.")
            await self.tick()

    def stop(self):
        if not self._started:
            return
        self.scheduler.shutdown(wait=False)
        self._started = False
        logger.info("Constituent snapshot scheduler stopped")


constituent_snapshot_scheduler = ConstituentSnapshotScheduler()
