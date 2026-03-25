from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from libs.platform.modules.option_chain_snapshot.src import (
    normalize_interval_boundary,
    parse_expiry_candidates,
    parse_option_rows,
    parse_spot_price,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.common.retry_mechanism.src import async_retry
from libs.utils.common.runtime_store.src import RuntimeSnapshotService
from libs.utils.config.src.fyers import (
    SNAPSHOT_EXPIRY_COUNT,
    SNAPSHOT_MAX_RETRIES,
    SNAPSHOT_RETRY_BASE_DELAY_SECONDS,
    SNAPSHOT_STRIKE_COUNT,
)

log = CustomLogger("OptionChainSnapshotService")
logger, listener = log.get_logger()
listener.start()


@dataclass
class SnapshotRuntimeStatus:
    is_running: bool = False
    last_run_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None


class OptionChainSnapshotService:
    _status = SnapshotRuntimeStatus()

    @classmethod
    def status(cls) -> dict:
        return asdict(cls._status)

    @classmethod
    async def capture_for_all_active_instruments(cls) -> dict:
        cls._status.is_running = True
        cls._status.last_run_at = datetime.now(timezone.utc).isoformat()

        try:
            instruments = InstrumentCatalogService.get_active_instruments()
            processed = 0
            total_snapshots = 0
            total_strikes = 0

            for instrument in instruments:
                result = await cls.capture_with_retries(instrument)
                processed += 1
                total_snapshots += result["snapshots"]
                total_strikes += result["strikes"]

            cls._status.last_success_at = datetime.now(timezone.utc).isoformat()
            cls._status.last_error = None
            return {
                "processed_instruments": processed,
                "snapshots_created": total_snapshots,
                "strikes_inserted": total_strikes,
            }
        except Exception as error:
            cls._status.last_error = str(error)
            raise
        finally:
            cls._status.is_running = False

    @classmethod
    async def capture_with_retries(cls, instrument) -> dict:
        """
        Captures option chain data with intelligent retry logic.

        Uses the optimized retry mechanism that:
        - Skips retry for client errors (400, 401, 403, 404, invalid symbols)
        - Uses exponential backoff for rate limits (429)
        - Uses exponential backoff for server errors and network issues
        """
        return await async_retry(
            cls.capture_for_instrument,
            instrument,
            max_retries=SNAPSHOT_MAX_RETRIES,
            base_delay=SNAPSHOT_RETRY_BASE_DELAY_SECONDS,
        )

    @classmethod
    async def capture_for_instrument(cls, instrument) -> dict:
        if not instrument.fyers_symbol:
            raise ValueError(
                f"Active instrument '{instrument.symbol}' does not have fyers_symbol configured"
            )

        captured_at = normalize_interval_boundary(datetime.now(timezone.utc))

        base_payload = await FyersClientService.fetch_option_chain(
            symbol=instrument.fyers_symbol,
            strike_count=SNAPSHOT_STRIKE_COUNT,
        )

        expiry_candidates = parse_expiry_candidates(base_payload)[
            :SNAPSHOT_EXPIRY_COUNT
        ]
        if not expiry_candidates:
            raise ValueError(
                f"No expiry candidates returned by FYERS for {instrument.fyers_symbol}"
            )

        snapshots_created = 0
        strikes_inserted = 0

        for index, candidate in enumerate(expiry_candidates):
            payload = base_payload
            timestamp = candidate.get("timestamp")
            if index > 0 and timestamp:
                payload = await FyersClientService.fetch_option_chain(
                    symbol=instrument.fyers_symbol,
                    strike_count=SNAPSHOT_STRIKE_COUNT,
                    timestamp=timestamp,
                )

            spot_price = parse_spot_price(payload)
            all_rows = parse_option_rows(payload)
            expiry_rows = [
                row
                for row in all_rows
                if row["expiry_date"] == candidate["expiry_date"]
            ]
            if not expiry_rows and index == 0:
                expiry_rows = all_rows
            if not expiry_rows:
                logger.warning(
                    "No option rows for expiry - "
                    f"fyers_symbol: {instrument.fyers_symbol} - "
                    f"expiry_date: {candidate['expiry_date']} - ",
                )
                continue

            if index == 0:
                await RuntimeSnapshotService.save_intraday_snapshot(
                    instrument=instrument,
                    captured_at=captured_at,
                    spot_price=spot_price,
                    strike_rows=expiry_rows,
                )
            snapshots_created += 1
            strikes_inserted += len(expiry_rows)

        if snapshots_created == 0:
            raise ValueError(
                f"No snapshots created for instrument {instrument.symbol}. "
                "FYERS response had no parsable strikes."
            )

        logger.info(
            "Snapshot captured - "
            f"symbol: {instrument.symbol} - "
            f"fyers_symbol: {instrument.fyers_symbol} - "
            f"snapshots: {snapshots_created} - "
            f"strikes: {strikes_inserted} - "
            f"captured_at: {captured_at.isoformat()}",
        )

        return {
            "instrument": instrument.symbol,
            "snapshots": snapshots_created,
            "strikes": strikes_inserted,
        }
