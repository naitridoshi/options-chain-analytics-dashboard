from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.market_state.src import MarketStateManager

log = CustomLogger("LiveMarketDataService")
logger, listener = log.get_logger()
listener.start()


class LiveMarketDataService:
    """Service for retrieving live market data from in-memory market state."""

    @classmethod
    def get_all_market_data(cls, market_state: MarketStateManager) -> dict:
        """Get all live market data (symbols + strikes).

        Args:
            market_state: MarketStateManager instance

        Returns:
            dict: {
                symbols: {symbol: {ltp, avg_price, volume, oi, bid, ask, last_update}, ...},
                strikes: {strike: {CE: {...}, PE: {...}}, ...}
            }
        """
        try:
            # Get symbol-level data
            symbols_data = {}
            for symbol, tick_data in market_state.get_all_symbols().items():
                symbols_data[symbol] = tick_data.to_dict()

            # Get strike-level data
            strikes_data = {}
            for strike, strike_data in market_state.get_all_strikes().items():
                strikes_data[strike] = strike_data.to_dict()

            return {
                "symbols": symbols_data,
                "strikes": strikes_data,
            }

        except Exception as error:
            logger.error(f"Failed to get market data - error: {str(error)}")
            raise

    @classmethod
    def get_symbols_only(cls, market_state: MarketStateManager) -> dict:
        """Get symbol-level live market data.

        Args:
            market_state: MarketStateManager instance

        Returns:
            dict: {symbol: {ltp, avg_price, volume, oi, bid, ask, last_update}, ...}
        """
        try:
            symbols_data = {}
            for symbol, tick_data in market_state.get_all_symbols().items():
                symbols_data[symbol] = tick_data.to_dict()

            return symbols_data

        except Exception as error:
            logger.error(f"Failed to get symbols data - error: {str(error)}")
            raise

    @classmethod
    def get_strikes_only(cls, market_state: MarketStateManager) -> dict:
        """Get strike-level live market data (CE + PE combined).

        Args:
            market_state: MarketStateManager instance

        Returns:
            dict: {strike: {CE: {...}, PE: {...}}, ...}
        """
        try:
            strikes_data = {}
            for strike, strike_data in market_state.get_all_strikes().items():
                strikes_data[strike] = strike_data.to_dict()

            return strikes_data

        except Exception as error:
            logger.error(f"Failed to get strikes data - error: {str(error)}")
            raise

    @classmethod
    def get_symbol_data(
        cls, symbol: str, market_state: MarketStateManager
    ) -> dict | None:
        """Get live market data for a specific symbol.

        Args:
            symbol: Option symbol (e.g., NSE:NIFTY24MAR22000CE)
            market_state: MarketStateManager instance

        Returns:
            dict or None: {ltp, avg_price, volume, oi, bid, ask, last_update} or None if not found
        """
        try:
            tick_data = market_state.get_symbol_data(symbol)

            if not tick_data:
                return None

            return tick_data.to_dict()

        except Exception as error:
            logger.error(
                f"Failed to get symbol data - symbol: {symbol}, error: {str(error)}"
            )
            raise

    @classmethod
    def get_strike_data(
        cls, strike: str, market_state: MarketStateManager
    ) -> dict | None:
        """Get live market data for a specific strike (CE + PE).

        Args:
            strike: Strike price (e.g., "22000")
            market_state: MarketStateManager instance

        Returns:
            dict or None: {CE: {...}, PE: {...}} or None if not found
        """
        try:
            strike_data = market_state.get_strike_data(strike)

            if not strike_data:
                return None

            return strike_data.to_dict()

        except Exception as error:
            logger.error(
                f"Failed to get strike data - strike: {strike}, error: {str(error)}"
            )
            raise
