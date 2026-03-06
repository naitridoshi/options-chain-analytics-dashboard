import asyncio
import signal
import sys
from pathlib import Path

BASE_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, BASE_DIR)

from apps.scheduler.platform.modules.option_chain_snapshot.src.scheduler import (  # noqa: E402
    snapshot_scheduler,
)


async def _run_forever():
    stop_event = asyncio.Event()

    def _handle_stop(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, _handle_stop)
    loop.add_signal_handler(signal.SIGTERM, _handle_stop)

    snapshot_scheduler.start()
    try:
        await stop_event.wait()
    finally:
        snapshot_scheduler.stop()


if __name__ == "__main__":
    asyncio.run(_run_forever())
