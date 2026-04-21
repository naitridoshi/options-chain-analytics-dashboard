from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal

from libs.platform.modules.option_chain_snapshot.src import (
    normalize_interval_boundary,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.index_constituent_catalog.src import (
    IndexConstituentCatalogService,
)
from libs.utils.common.retry_mechanism.src import async_retry
from libs.utils.common.runtime_store.src import RuntimeConstituentService
from libs.utils.config.src.fyers import (
    SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
    SNAPSHOT_MAX_RETRIES,
    SNAPSHOT_RETRY_BASE_DELAY_SECONDS,
)

log = CustomLogger("ConstituentSnapshotService")
logger, listener = log.get_logger()
listener.start()


@dataclass
class ConstituentSnapshotRuntimeStatus:
    is_running: bool = False
    last_run_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None


class ConstituentSnapshotService:
    _status = ConstituentSnapshotRuntimeStatus()

    @classmethod
    def status(cls) -> dict:
        return asdict(cls._status)

    @classmethod
    async def capture_for_all_constituents(cls) -> dict:
        cls._status.is_running = True
        cls._status.last_run_at = datetime.now(timezone.utc).isoformat()
        try:
            constituents = IndexConstituentCatalogService.get_all_unique_constituents()
            captured_at = normalize_interval_boundary(
                datetime.now(timezone.utc),
                interval_seconds=SCRIPTS_SNAPSHOT_INTERVAL_SECONDS,
            )
            previous_close_map = (
                await RuntimeConstituentService.get_previous_close_reference_map(
                    captured_at_utc=captured_at,
                )
            )

            snapshot_rows: list[dict] = []
            processed = 0
            failed = 0
            for constituent in constituents:
                try:
                    ltp = await cls.fetch_ltp_with_retries(constituent)
                except Exception as e:
                    logger.warning(f"Failed to fetch LTP for {constituent.symbol}: {e}")
                    ltp = None
                    failed += 1
                previous_close = previous_close_map.get(constituent.symbol)
                change = None
                change_pct = None
                if ltp is not None and previous_close is not None:
                    previous_close_decimal = Decimal(str(previous_close))
                    change = ltp - previous_close_decimal
                    if previous_close_decimal != 0:
                        change_pct = (change / previous_close_decimal) * Decimal("100")
                snapshot_rows.append(
                    {
                        "symbol": constituent.symbol,
                        "name": constituent.name,
                        "fyers_symbol": constituent.fyers_symbol,
                        "ltp": ltp,
                        "previous_close": Decimal(str(previous_close))
                        if previous_close is not None
                        else None,
                        "change": change,
                        "change_pct": change_pct,
                    }
                )
                processed += 1

            await RuntimeConstituentService.save_intraday_snapshot(
                captured_at=captured_at,
                script_rows=snapshot_rows,
            )

            cls._status.last_success_at = datetime.now(timezone.utc).isoformat()
            cls._status.last_error = None
            return {
                "processed_constituents": processed,
                "snapshots_created": len(snapshot_rows),
                "failed_constituents": failed,
            }
        except Exception as error:
            cls._status.last_error = str(error)
            raise
        finally:
            cls._status.is_running = False

    @classmethod
    async def fetch_ltp_with_retries(cls, constituent):
        return await async_retry(
            FyersClientService.fetch_quote,
            symbol=constituent.fyers_symbol,
            max_retries=SNAPSHOT_MAX_RETRIES,
            base_delay=SNAPSHOT_RETRY_BASE_DELAY_SECONDS,
        )

    @classmethod
    async def get_constituents_for_index(cls, index_name: str) -> dict:
        return await RuntimeConstituentService.get_constituents_for_index(index_name)
