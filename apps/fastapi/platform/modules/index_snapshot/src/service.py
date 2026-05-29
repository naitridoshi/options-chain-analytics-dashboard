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
from libs.utils.common.runtime_store.src import (
    RuntimeIndexSnapshotService,
    RuntimeScriptSnapshotService,
)
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

    BATCH_SIZE = 20

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

            # Batch-fetch LTP + prev_close from Fyers API (primary source)
            quotes_map: dict[str, dict[str, Decimal | None]] = {}
            fyers_symbols = [idx.fyers_symbol for idx in indices]
            batches = [
                fyers_symbols[i : i + cls.BATCH_SIZE]
                for i in range(0, len(fyers_symbols), cls.BATCH_SIZE)
            ]
            for batch in batches:
                try:
                    batch_quotes = await async_retry(
                        FyersClientService.fetch_quotes_batch_with_prev_close,
                        symbols=batch,
                        max_retries=SNAPSHOT_MAX_RETRIES,
                        base_delay=SNAPSHOT_RETRY_BASE_DELAY_SECONDS,
                    )
                    quotes_map.update(batch_quotes)
                except Exception as e:
                    logger.warning(f"Failed to fetch index batch: {e}")

            # Fallback: Redis snapshots for any symbols missing API prev_close
            needs_fallback = any(
                q.get("prev_close") is None for q in quotes_map.values()
            )
            previous_close_map: dict[str, float | None] = {}
            if needs_fallback:
                previous_close_map = (
                    await RuntimeIndexSnapshotService.get_previous_close_reference_map(
                        captured_at_utc=captured_at,
                    )
                )

            snapshot_rows: list[dict] = []
            for index in indices:
                quote = quotes_map.get(index.fyers_symbol, {})
                ltp = quote.get("ltp")
                # Primary: API prev_close_price; Fallback: Redis snapshot
                prev_close = quote.get("prev_close")
                if prev_close is None:
                    prev_close_raw = previous_close_map.get(index.symbol)
                    if prev_close_raw is not None:
                        prev_close = Decimal(str(prev_close_raw))
                change = None
                change_pct = None
                if ltp is not None and prev_close is not None:
                    change = ltp - prev_close
                    if prev_close != 0:
                        change_pct = (change / prev_close) * Decimal("100")
                snapshot_rows.append(
                    {
                        "symbol": index.symbol,
                        "name": index.name or index.symbol,
                        "category": index.category,
                        "fyers_symbol": index.fyers_symbol,
                        "ltp": ltp,
                        "previous_close": prev_close,
                        "change": change,
                        "change_pct": change_pct,
                    }
                )
            await RuntimeIndexSnapshotService.save_intraday_snapshot(
                captured_at=captured_at,
                index_rows=snapshot_rows,
            )

            cls._status.last_success_at = datetime.now(timezone.utc).isoformat()
            cls._status.last_error = None
            return {
                "processed_indices": len(indices),
                "snapshots_created": len(snapshot_rows),
            }
        except Exception as error:
            cls._status.last_error = str(error)
            raise
        finally:
            cls._status.is_running = False

    @classmethod
    async def get_heatmap_data(cls, category: str | None = None) -> dict:
        return await RuntimeIndexSnapshotService.get_heatmap_data(category=category)

    @classmethod
    async def get_breadth_summary(cls) -> dict:
        index_snapshot = await RuntimeIndexSnapshotService.get_latest_snapshot()
        index_breadth = await RuntimeIndexSnapshotService.get_breadth_by_category(
            snapshot=index_snapshot
        )
        script_breadth = await RuntimeScriptSnapshotService.get_breadth_by_category()
        return {
            "captured_at": index_snapshot.get("captured_at"),
            "broad_market": {
                "indices": index_breadth.get(
                    "BROAD_MARKET",
                    {"advance": 0, "decline": 0, "unchanged": 0, "total": 0},
                ),
                "scripts": script_breadth.get(
                    "BROAD_MARKET",
                    {"advance": 0, "decline": 0, "unchanged": 0, "total": 0},
                ),
            },
            "sectoral": {
                "indices": index_breadth.get(
                    "SECTORAL", {"advance": 0, "decline": 0, "unchanged": 0, "total": 0}
                ),
                "scripts": script_breadth.get(
                    "SECTORAL", {"advance": 0, "decline": 0, "unchanged": 0, "total": 0}
                ),
            },
        }
