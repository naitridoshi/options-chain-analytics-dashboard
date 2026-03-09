from datetime import datetime, timezone

from libs.platform.modules.option_chain_snapshot.src import (
    normalize_interval_boundary,
    parse_expiry_candidates,
    parse_option_rows,
    parse_spot_price,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.market_state.src import get_market_state_manager
from libs.utils.config.src.fyers import (
    SNAPSHOT_EXPIRY_COUNT,
    SNAPSHOT_STRIKE_COUNT,
)
from libs.utils.db.postgres.operations.src import (
    InstrumentOperations,
    OptionSnapshotOperations,
)

log = CustomLogger("SnapshotMergeService")
logger, listener = log.get_logger()
listener.start()


class SnapshotMergeService:
    """Merges REST option chain data with WebSocket market data into snapshots."""

    @classmethod
    async def capture_with_merged_market_data(cls) -> dict:
        """Capture snapshots for all instruments with merged market data.

        Fetches option chain via REST API and enriches it with WebSocket market data
        (LTP, avg_price) before storing snapshots.

        Returns:
            dict: {
                processed_instruments: int,
                snapshots_created: int,
                strikes_inserted: int
            }
        """
        try:
            market_state = get_market_state_manager()

            # Get all active instruments
            instruments = await InstrumentOperations.get_active_instruments()
            processed = 0
            total_snapshots = 0
            total_strikes = 0

            for instrument in instruments:
                result = await cls.capture_for_instrument_with_merge(
                    instrument, market_state
                )
                if result:
                    processed += 1
                    total_snapshots += result["snapshots"]
                    total_strikes += result["strikes"]

            logger.info(
                f"Snapshot capture with merge completed - "
                f"processed_instruments: {processed}, "
                f"snapshots_created: {total_snapshots}, "
                f"strikes_inserted: {total_strikes}"
            )

            return {
                "processed_instruments": processed,
                "snapshots_created": total_snapshots,
                "strikes_inserted": total_strikes,
            }

        except Exception as error:
            logger.error(
                f"Failed to capture snapshots with merge - error: {str(error)}"
            )
            raise

    @classmethod
    async def capture_for_instrument_with_merge(
        cls, instrument, market_state
    ) -> dict | None:
        """Capture and merge snapshot for a single instrument.

        Args:
            instrument: Instrument object
            market_state: MarketStateManager instance

        Returns:
            dict or None: {snapshots, strikes} or None on error
        """
        try:
            if not instrument.fyers_symbol:
                logger.warning(f"Instrument {instrument.symbol} has no fyers_symbol")
                return None

            captured_at = normalize_interval_boundary(datetime.now(timezone.utc))

            # Fetch option chain via REST
            base_payload = await FyersClientService.fetch_option_chain(
                symbol=instrument.fyers_symbol,
                strike_count=SNAPSHOT_STRIKE_COUNT,
            )

            expiry_candidates = parse_expiry_candidates(base_payload)[
                :SNAPSHOT_EXPIRY_COUNT
            ]
            if not expiry_candidates:
                logger.warning(f"No expiry candidates for {instrument.fyers_symbol}")
                return None

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
                        f"No option rows for expiry - "
                        f"fyers_symbol: {instrument.fyers_symbol} - "
                        f"expiry_date: {candidate['expiry_date']}"
                    )
                    continue

                # Enrich strike rows with market data from WebSocket
                enriched_rows = cls.enrich_strike_rows_with_market_data(
                    expiry_rows, market_state
                )

                # Store enriched snapshot
                snapshot_result = (
                    await OptionSnapshotOperations.create_snapshot_transactional(
                        instrument_id=instrument.id,
                        expiry_date=candidate["expiry_date"],
                        is_weekly=bool(candidate.get("is_weekly", True)),
                        captured_at=captured_at,
                        spot_price=spot_price,
                        strike_rows=enriched_rows,
                    )
                )
                snapshots_created += 1
                strikes_inserted += snapshot_result["strikes_inserted"]

            if snapshots_created == 0:
                logger.warning(f"No snapshots created for {instrument.symbol}")
                return None

            logger.info(
                f"Instrument snapshot captured with merge - "
                f"symbol: {instrument.symbol}, "
                f"snapshots: {snapshots_created}, "
                f"strikes: {strikes_inserted}"
            )

            return {
                "snapshots": snapshots_created,
                "strikes": strikes_inserted,
            }

        except Exception as error:
            logger.error(
                f"Failed to capture instrument snapshot with merge - "
                f"instrument: {instrument.symbol}, "
                f"error: {str(error)}"
            )
            return None

    @classmethod
    def enrich_strike_rows_with_market_data(
        cls, strike_rows: list[dict], market_state
    ) -> list[dict]:
        """Enrich strike rows with market data from WebSocket.

        Adds avg_price from WebSocket market data. Preserves REST bid/ask/bid_qty/ask_qty
        as a unit to maintain data consistency.

        Args:
            strike_rows: Strike data from REST API
            market_state: MarketStateManager instance

        Returns:
            list: Enriched strike rows with avg_price from market state
        """
        enriched_rows = []

        for row in strike_rows:
            enriched_row = row.copy()

            # Try to find market state for this strike/option type
            strike_price = row.get("strike_price")
            option_type = row.get("option_type")

            if strike_price and option_type:
                strike_str = str(int(float(strike_price)))

                # Get strike state (CE + PE combined)
                strike_data = market_state.get_strike_data(strike_str)

                if strike_data:
                    # Only enrich with avg_price from WebSocket.
                    # We do NOT overwrite bid/ask because REST provides bid_qty/ask_qty
                    # as paired data. Mixing WebSocket bid/ask with REST bid_qty/ask_qty
                    # would create data consistency issues.
                    if option_type == "CE" and strike_data.ce_data:
                        if strike_data.ce_data.avg_price:
                            enriched_row["avg_price"] = float(
                                strike_data.ce_data.avg_price
                            )

                    elif option_type == "PE" and strike_data.pe_data:
                        if strike_data.pe_data.avg_price:
                            enriched_row["avg_price"] = float(
                                strike_data.pe_data.avg_price
                            )

            enriched_rows.append(enriched_row)

        return enriched_rows
