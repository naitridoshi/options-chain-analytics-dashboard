import asyncio
import signal
import sys

from apps.scheduler.platform.modules.index_constituent.src import (
    constituent_snapshot_scheduler,
)
from apps.scheduler.platform.modules.index_snapshot.src import (
    index_snapshot_scheduler,
)
from apps.scheduler.platform.modules.option_chain_snapshot.src import (
    snapshot_scheduler,
)
from apps.scheduler.platform.modules.script_snapshot.src import (
    script_snapshot_scheduler,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.db.redis.src.client import redis_client_manager

log = CustomLogger("SchedulerApp")
logger, listener = log.get_logger()
listener.start()


async def run_scheduler() -> None:
    stop_event = asyncio.Event()

    def _handle_stop(*_) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _handle_stop)
        loop.add_signal_handler(signal.SIGTERM, _handle_stop)

    for name, scheduler in [
        ("OptionChainSnapshot", snapshot_scheduler),
        ("ScriptSnapshot", script_snapshot_scheduler),
        ("IndexSnapshot", index_snapshot_scheduler),
        ("ConstituentSnapshot", constituent_snapshot_scheduler),
    ]:
        for attempt in range(1, 4):
            try:
                await scheduler.start()
                break
            except Exception as e:
                if attempt == 3:
                    logger.error(
                        f"{name} scheduler failed to start after 3 attempts - "
                        f"error: {e}"
                    )
                else:
                    logger.warning(
                        f"{name} scheduler start failed (attempt {attempt}/3) - "
                        f"error: {e} - retrying in 10s..."
                    )
                    await asyncio.sleep(10)
    try:
        await stop_event.wait()
    finally:
        snapshot_scheduler.stop()
        script_snapshot_scheduler.stop()
        index_snapshot_scheduler.stop()
        constituent_snapshot_scheduler.stop()
        await redis_client_manager.close()
