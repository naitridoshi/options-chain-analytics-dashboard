from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from apps.ingestion.platform.modules.snapshot_merge.src.snapshot_merge_service import (
    SnapshotMergeService,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.config.src.fyers import INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS
from libs.utils.state.src import AppState

log = CustomLogger("SnapshotMergeScheduler")
logger, listener = log.get_logger()
listener.start()


class SnapshotMergeScheduler:
    """Scheduler for periodic snapshot merge operations (REST + WebSocket data).

    Runs every INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS to merge REST option chain
    data with WebSocket market data and store snapshots with market-informed metrics.
    """

    def __init__(self, app_state: AppState):
        self._app_state = app_state
        self._scheduler = AsyncIOScheduler()
        self._is_running = False

    async def start(self) -> None:
        """Start the snapshot merge scheduler."""
        try:
            interval_seconds = INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS

            logger.info(
                f"Starting snapshot merge scheduler - interval_seconds: {interval_seconds}"
            )

            # Schedule job to run every N seconds
            self._scheduler.add_job(
                self._merge_snapshots_job,
                trigger=IntervalTrigger(seconds=interval_seconds),
                id="snapshot_merge_job",
                name="Merge REST and WebSocket snapshot data",
                replace_existing=True,
            )

            self._scheduler.start()
            self._is_running = True

            logger.info("Snapshot merge scheduler started successfully")

        except Exception as error:
            logger.error(
                f"Failed to start snapshot merge scheduler - error: {str(error)}"
            )
            raise

    async def stop(self) -> None:
        """Stop the snapshot merge scheduler."""
        try:
            if self._is_running and self._scheduler.running:
                self._scheduler.shutdown(wait=True)
                self._is_running = False
                logger.info("Snapshot merge scheduler stopped")

        except Exception as error:
            logger.error(
                f"Error stopping snapshot merge scheduler - error: {str(error)}"
            )

    async def _merge_snapshots_job(self) -> None:
        """Job that merges REST and WebSocket snapshot data."""
        try:
            logger.info("Running snapshot merge job")

            result = await SnapshotMergeService.capture_with_merged_market_data(
                market_state=self._app_state.market_state
            )

            logger.info(
                f"Snapshot merge job completed - "
                f"processed_instruments: {result['processed_instruments']}, "
                f"snapshots: {result['snapshots_created']}, "
                f"strikes: {result['strikes_inserted']}"
            )

        except Exception as error:
            logger.error(f"Snapshot merge job failed - error: {str(error)}")

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._is_running


def get_snapshot_merge_scheduler(app_state: AppState) -> SnapshotMergeScheduler:
    """Create SnapshotMergeScheduler with injected state.

    Args:
        app_state: Application state container

    Returns:
        SnapshotMergeScheduler: New scheduler instance
    """
    return SnapshotMergeScheduler(app_state=app_state)
