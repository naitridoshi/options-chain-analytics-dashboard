from __future__ import annotations

import asyncio
import signal
import sys

from apps.live_market_data.platform.modules.housekeeping.src.service import (
    LiveMarketHousekeepingService,
)
from apps.live_market_data.platform.modules.runtime.src.service import (
    LiveMarketDataRuntimeService,
)
from apps.live_market_data.platform.modules.streaming.src.service import (
    LiveMarketStreamingService,
)
from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("LiveMarketDataApp")
logger, listener = log.get_logger()
listener.start()


async def run_live_market_data_app() -> None:
    service = LiveMarketDataRuntimeService()
    housekeeping_service = LiveMarketHousekeepingService()
    streaming_service = LiveMarketStreamingService()
    stop_event = asyncio.Event()

    def _handle_stop(*_) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _handle_stop)
        loop.add_signal_handler(signal.SIGTERM, _handle_stop)

    await service.start()
    await housekeeping_service.start()
    await streaming_service.start()
    try:
        logger.info("Live market data app running, waiting for shutdown signal...")
        while not stop_event.is_set():
            await service.heartbeat(
                "running",
                details={
                    "streaming": await streaming_service.get_status(),
                    "housekeeping_running": True,
                },
            )
            await asyncio.sleep(15)
    finally:
        await streaming_service.stop()
        await housekeeping_service.stop()
        await service.stop()
