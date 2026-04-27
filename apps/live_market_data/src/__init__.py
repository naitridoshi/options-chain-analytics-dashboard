from __future__ import annotations

import asyncio
import signal
import sys
import threading

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

# Global stop event for cross-thread signaling on Windows
_stop_event: asyncio.Event | None = None
_stop_lock = threading.Lock()


def _signal_handler_windows(signum, frame) -> None:
    """Signal handler for Windows - sets stop event from any thread."""
    global _stop_event
    with _stop_lock:
        if _stop_event is not None and not _stop_event.is_set():
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            # Call set() in a thread-safe manner
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(_stop_event.set)
            except RuntimeError:
                # No running loop, already shutting down
                pass


def _signal_handler_unix(
    loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event
) -> None:
    """Signal handler factory for Unix - sets stop event in the event loop."""

    def handler():
        logger.info("Received shutdown signal, initiating graceful shutdown...")
        stop_event.set()

    return handler


async def run_live_market_data_app() -> None:
    global _stop_event

    service = LiveMarketDataRuntimeService()
    housekeeping_service = LiveMarketHousekeepingService()
    streaming_service = LiveMarketStreamingService()
    stop_event = asyncio.Event()
    _stop_event = stop_event

    loop = asyncio.get_running_loop()

    # Platform-specific signal handling
    if sys.platform != "win32":
        # Unix: Use loop.add_signal_handler for clean async shutdown
        loop.add_signal_handler(signal.SIGINT, _signal_handler_unix(loop, stop_event))
        loop.add_signal_handler(signal.SIGTERM, _signal_handler_unix(loop, stop_event))
    else:
        # Windows: Use signal.signal with wakeup fd for async support
        # Set up wakeup file descriptor to allow signal handling in async context
        try:
            # Create a socket pair for waking up the event loop
            import socket

            reader, writer = socket.socketpair()
            reader.setblocking(False)
            writer.setblocking(False)
            loop.add_reader(reader.fileno(), lambda: reader.recv(1024))

            def windows_signal_handler(signum, frame):
                logger.info(
                    f"Received signal {signum}, initiating graceful shutdown..."
                )
                writer.send(b"\x00")  # Wake up the event loop
                stop_event.set()

            signal.signal(signal.SIGINT, windows_signal_handler)
            signal.signal(signal.SIGTERM, windows_signal_handler)
            signal.signal(
                signal.SIGBREAK, windows_signal_handler
            )  # Windows-specific Ctrl+Break
        except Exception as e:
            # Fallback: basic signal handling without wakeup
            logger.warning(
                f"Could not set up Windows async signal handling: {e}, using fallback"
            )
            signal.signal(signal.SIGINT, _signal_handler_windows)
            signal.signal(signal.SIGTERM, _signal_handler_windows)
            signal.signal(signal.SIGBREAK, _signal_handler_windows)

    # Startup with retry for Redis-dependent operations
    for attempt in range(1, 6):
        try:
            await service.start()
            break
        except Exception as e:
            if attempt == 5:
                logger.error(f"Runtime service startup failed after 5 attempts - {e}")
                raise
            logger.warning(
                f"Runtime service startup failed (attempt {attempt}/5) - "
                f"error: {e} - retrying in 10s..."
            )
            await asyncio.sleep(10)

    try:
        await housekeeping_service.start()
    except Exception as e:
        logger.error(f"Housekeeping service start failed - error: {e}")

    try:
        await streaming_service.start()
    except Exception as e:
        logger.error(f"Streaming service start failed - error: {e}")

    try:
        logger.info("Live market data app running, waiting for shutdown signal...")
        while not stop_event.is_set():
            try:
                await service.heartbeat(
                    "running",
                    details={
                        "streaming": await streaming_service.get_status(),
                        "housekeeping_running": True,
                    },
                )
            except Exception as e:
                logger.error(f"Heartbeat write failed - error: {e}")
            await asyncio.sleep(15)
    except asyncio.CancelledError:
        logger.info("Main task cancelled, initiating shutdown...")
    finally:
        logger.info("Shutting down live market data app...")
        await streaming_service.stop()
        await housekeeping_service.stop()
        await service.stop()
        _stop_event = None
        logger.info("Live market data app stopped gracefully")
