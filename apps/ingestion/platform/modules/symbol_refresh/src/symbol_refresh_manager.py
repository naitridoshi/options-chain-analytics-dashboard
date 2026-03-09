from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.option_symbols.src import (
    OptionSymbolGenerator,
    get_symbol_mapping_service,
)
from libs.utils.config.src.fyers import SNAPSHOT_STRIKE_COUNT
from libs.utils.db.postgres.operations.src import InstrumentOperations

log = CustomLogger("SymbolRefreshManager")
logger, listener = log.get_logger()
listener.start()


class SymbolRefreshManager:
    """Manages daily symbol refresh for option chains."""

    @classmethod
    async def refresh_symbols_for_all_instruments(
        cls,
    ) -> dict[str, dict]:
        """Refresh symbols for all active instruments.

        Returns:
            dict: {
                instrument_id: {
                    fyers_symbol: str,
                    expiry_date: str,
                    symbols: list[str],
                    symbol_mapping: dict
                }
            }
        """
        try:
            instruments = await InstrumentOperations.get_active_instruments()
            refresh_results = {}

            for instrument in instruments:
                result = await cls.refresh_symbols_for_instrument(instrument)
                refresh_results[str(instrument.id)] = result

            logger.info(
                f"Symbol refresh completed - "
                f"instruments: {len(instruments)}, "
                f"successful: {len([r for r in refresh_results.values() if r])}"
            )

            return refresh_results

        except Exception as error:
            logger.error(
                f"Failed to refresh symbols for all instruments - error: {str(error)}"
            )
            raise

    @classmethod
    async def refresh_symbols_for_instrument(cls, instrument) -> dict | None:
        """Refresh symbols for a single instrument.

        Args:
            instrument: Instrument object

        Returns:
            dict or None: Refresh result
        """
        try:
            if not instrument.fyers_symbol:
                logger.warning(f"Instrument {instrument.symbol} has no fyers_symbol")
                return None

            # Fetch option chain
            logger.info(
                f"Fetching option chain for refresh - "
                f"instrument: {instrument.symbol}, "
                f"fyers_symbol: {instrument.fyers_symbol}"
            )

            chain_data = await FyersClientService.fetch_option_chain(
                symbol=instrument.fyers_symbol,
                strike_count=SNAPSHOT_STRIKE_COUNT,
            )

            # Generate symbols
            symbol_result = OptionSymbolGenerator.generate_symbols_from_chain(
                fyers_symbol=instrument.fyers_symbol,
                chain_data=chain_data,
                strike_count=SNAPSHOT_STRIKE_COUNT,
            )

            # Update mapping service
            mapping_service = get_symbol_mapping_service()
            expiry_date = symbol_result["expiry_date"]
            old_mapping = mapping_service.get_current_mapping()

            mapping_service.update_mapping(
                instrument_symbol=instrument.symbol,
                expiry_date=expiry_date,
                symbol_mapping=symbol_result["symbol_mapping"],
                all_symbols=symbol_result["symbols"],
            )

            # Detect expiry change
            expiry_changed = (
                old_mapping is None or old_mapping.expiry_date != expiry_date
            )

            if expiry_changed:
                logger.warning(
                    f"Expiry change detected - "
                    f"instrument: {instrument.symbol}, "
                    f"old_expiry: {old_mapping.expiry_date if old_mapping else 'None'}, "
                    f"new_expiry: {expiry_date}"
                )

            return {
                "fyers_symbol": instrument.fyers_symbol,
                "expiry_date": expiry_date,
                "symbols": symbol_result["symbols"],
                "symbol_mapping": symbol_result["symbol_mapping"],
                "expiry_changed": expiry_changed,
            }

        except Exception as error:
            logger.error(
                f"Failed to refresh symbols for instrument - "
                f"instrument: {instrument.symbol}, "
                f"error: {str(error)}"
            )
            return None

    @classmethod
    async def detect_expiry_changes(
        cls, refresh_results: dict[str, dict]
    ) -> list[dict]:
        """Detect which instruments had expiry changes.

        Args:
            refresh_results: Results from refresh_symbols_for_all_instruments

        Returns:
            list: Instruments with expiry changes
        """
        expiry_changes = [
            result
            for result in refresh_results.values()
            if result and result.get("expiry_changed")
        ]
        return expiry_changes
