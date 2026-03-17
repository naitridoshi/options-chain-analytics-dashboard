import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from libs.platform.modules.option_chain_snapshot.src import (
    normalize_interval_boundary,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.config.src.fyers import (
    SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
    SNAPSHOT_MAX_RETRIES,
    SNAPSHOT_RETRY_BASE_DELAY_SECONDS,
)
from libs.utils.db.postgres.operations.src import (
    ScriptOperations,
    ScriptSnapshotOperations,
)

log = CustomLogger("ScriptSnapshotService")
logger, listener = log.get_logger()
listener.start()


@dataclass
class ScriptSnapshotRuntimeStatus:
    is_running: bool = False
    last_run_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None


class ScriptSnapshotService:
    _status = ScriptSnapshotRuntimeStatus()

    @classmethod
    def status(cls) -> dict:
        return asdict(cls._status)

    @classmethod
    async def capture_for_all_active_scripts(cls) -> dict:
        cls._status.is_running = True
        cls._status.last_run_at = datetime.now(timezone.utc).isoformat()
        try:
            scripts = await ScriptOperations.get_active_scripts()
            captured_at = normalize_interval_boundary(
                datetime.now(timezone.utc),
                interval_seconds=SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
            )
            previous_close_map = await ScriptSnapshotOperations.get_previous_close_reference_map_for_scripts(
                script_ids=[script.id for script in scripts],
                captured_at_utc=captured_at,
            )

            snapshot_rows = []
            processed = 0
            for script in scripts:
                ltp = await cls.fetch_ltp_with_retries(script)
                snapshot_rows.append(
                    {
                        "script_id": script.id,
                        "captured_at": captured_at,
                        "ltp": ltp,
                        "previous_close": previous_close_map.get(script.id),
                    }
                )
                processed += 1
            snapshots_created = (
                await ScriptSnapshotOperations.create_script_snapshots_bulk(
                    snapshots=snapshot_rows
                )
            )

            cls._status.last_success_at = datetime.now(timezone.utc).isoformat()
            cls._status.last_error = None
            return {
                "processed_scripts": processed,
                "snapshots_created": snapshots_created,
            }
        except Exception as error:
            cls._status.last_error = str(error)
            raise
        finally:
            cls._status.is_running = False

    @classmethod
    async def fetch_ltp_with_retries(cls, script):
        max_retries = max(1, SNAPSHOT_MAX_RETRIES)
        for attempt in range(1, max_retries + 1):
            try:
                return await FyersClientService.fetch_quote(symbol=script.fyers_symbol)
            except Exception as error:
                if attempt >= max_retries:
                    logger.error(
                        "Script quote retries exhausted - "
                        f"symbol: {script.symbol} - attempts: {attempt} - error: {str(error)}"
                    )
                    raise
                delay = SNAPSHOT_RETRY_BASE_DELAY_SECONDS * attempt
                logger.warning(
                    "Script quote attempt failed, retrying - "
                    f"symbol: {script.symbol} - attempts: {attempt} - error: {str(error)}"
                )
                await asyncio.sleep(delay)

    @classmethod
    async def get_advance_decline(cls) -> dict:
        return await ScriptSnapshotOperations.get_latest_advance_decline()
