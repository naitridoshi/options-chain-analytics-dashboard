from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal

from libs.platform.modules.option_chain_snapshot.src import (
    normalize_interval_boundary,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.index_catalog.src import IndexCatalogService
from libs.utils.common.retry_mechanism.src import async_retry
from libs.utils.common.runtime_store.src import RuntimeIndexSnapshotService
from libs.utils.config.src.fyers import (
    SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
    SNAPSHOT_MAX_RETRIES,
    SNAPSHOT_RETRY_BASE_DELAY_SECONDS,
)

log = CustomLogger("IndexSnapshotService")
logger, listener = log.get_logger()
listener.start()


@dataclass
class IndexSnapshotRuntimeStatus:
    is_running: bool = False
    last_run_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None


class IndexSnapshotService:
    _status = IndexSnapshotRuntimeStatus()

    @classmethod
    def status(cls) -> dict:
        return asdict(cls._status)

    @classmethod
    async def capture_for_all_active_indices(cls) -> dict:
        cls._status.is_running = True
        cls._status.last_run_at = datetime.now(timezone.utc).isoformat()
        try:
            indices = IndexCatalogService.get_active_indices()
            captured_at = normalize_interval_boundary(
                datetime.now(timezone.utc),
                interval_seconds=SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
            )
            previous_close_map = (
                await RuntimeIndexSnapshotService.get_previous_close_reference_map(
                    captured_at_utc=captured_at,
                )
            )

            snapshot_rows: list[dict] = []
            processed = 0
            for index in indices:
                ltp = await cls.fetch_ltp_with_retries(index)
                previous_close = previous_close_map.get(index.symbol)
                change = None
                change_pct = None
                if previous_close is not None:
                    previous_close_decimal = Decimal(str(previous_close))
                    change = ltp - previous_close_decimal
                    if previous_close_decimal != 0:
                        change_pct = (change / previous_close_decimal) * Decimal("100")
                snapshot_rows.append(
                    {
                        "symbol": index.symbol,
                        "name": index.name or index.symbol,
                        "category": index.category,
                        "fyers_symbol": index.fyers_symbol,
                        "ltp": ltp,
                        "previous_close": Decimal(str(previous_close))
                        if previous_close is not None
                        else None,
                        "change": change,
                        "change_pct": change_pct,
                    }
                )
                processed += 1
            await RuntimeIndexSnapshotService.save_intraday_snapshot(
                captured_at=captured_at,
                index_rows=snapshot_rows,
            )
            snapshots_created = len(snapshot_rows)

            cls._status.last_success_at = datetime.now(timezone.utc).isoformat()
            cls._status.last_error = None
            return {
                "processed_indices": processed,
                "snapshots_created": snapshots_created,
            }
        except Exception as error:
            cls._status.last_error = str(error)
            raise
        finally:
            cls._status.is_running = False

    @classmethod
    async def fetch_ltp_with_retries(cls, index):
        """
        Fetch LTP with intelligent retry logic.

        Uses the optimized retry mechanism that:
        - Skips retry for client errors (400, 401, 403, 404, invalid symbols)
        - Uses exponential backoff for rate limits (429)
        - Uses exponential backoff for server errors and network issues
        """
        return await async_retry(
            FyersClientService.fetch_quote,
            symbol=index.fyers_symbol,
            max_retries=SNAPSHOT_MAX_RETRIES,
            base_delay=SNAPSHOT_RETRY_BASE_DELAY_SECONDS,
        )

    @classmethod
    async def get_heatmap_data(cls, category: str | None = None) -> dict:
        return await RuntimeIndexSnapshotService.get_heatmap_data(category=category)
