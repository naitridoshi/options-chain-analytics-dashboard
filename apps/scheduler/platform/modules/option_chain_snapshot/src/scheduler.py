from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from apps.fastapi.platform.modules.option_chain_snapshot.src.service import (
    OptionChainSnapshotService,
)
from libs.platform.modules.option_chain_snapshot.src import (
    IST,
    is_market_open_now,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.config.src.fyers import SNAPSHOT_INTERVAL_MINUTES

log = CustomLogger("OptionChainSnapshotScheduler")
logger, listener = log.get_logger()
listener.start()


class OptionChainSnapshotScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=IST)
        self._started = False

    async def tick(self):
        if not is_market_open_now():
            return
        await OptionChainSnapshotService.capture_for_all_active_instruments()

    def start(self):
        if self._started:
            return
        self.scheduler.add_job(
            self.tick,
            trigger=IntervalTrigger(minutes=SNAPSHOT_INTERVAL_MINUTES),
            id="options-chain-snapshot",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        self.scheduler.start()
        self._started = True
        logger.info("Option chain snapshot scheduler started")

    def stop(self):
        if not self._started:
            return
        self.scheduler.shutdown(wait=False)
        self._started = False
        logger.info("Option chain snapshot scheduler stopped")


snapshot_scheduler = OptionChainSnapshotScheduler()
