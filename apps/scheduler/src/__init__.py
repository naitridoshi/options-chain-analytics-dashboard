import asyncio
import signal

from apps.scheduler.platform.modules.option_chain_snapshot.src import (
    snapshot_scheduler,
)
from apps.scheduler.platform.modules.script_snapshot.src import (
    script_snapshot_scheduler,
)


async def run_scheduler() -> None:
    stop_event = asyncio.Event()

    def _handle_stop(*_) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, _handle_stop)
    loop.add_signal_handler(signal.SIGTERM, _handle_stop)

    await snapshot_scheduler.start()
    await script_snapshot_scheduler.start()
    try:
        await stop_event.wait()
    finally:
        snapshot_scheduler.stop()
        script_snapshot_scheduler.stop()
