from datetime import date
from decimal import Decimal

from libs.platform.modules.option_chain_snapshot.src import (
    parse_expiry_candidates,
    parse_option_rows,
    parse_spot_price,
)
from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("OptionSymbolGenerator")
logger, listener = log.get_logger()
listener.start()


class OptionSymbolGenerator:
    """Generates FYERS option chain symbols from raw option chain data.

    Uses existing parsing functions from option_chain_snapshot module to ensure
    consistent handling of FYERS API response structure.
    """

    @classmethod
    def _find_atm_strike(
        cls, strike_prices: list[Decimal], spot_price: Decimal
    ) -> Decimal:
        """Find the ATM strike (closest to spot price).

        Args:
            strike_prices: List of available strike prices
            spot_price: Current spot price

        Returns:
            Decimal: The strike price closest to spot
        """
        return min(strike_prices, key=lambda s: abs(s - spot_price))

    @classmethod
    def _select_strikes_around_atm(
        cls,
        strike_prices: list[Decimal],
        atm_strike: Decimal,
        count: int,
    ) -> list[Decimal]:
        """Select strikes centered around ATM.

        Selects `count` strikes below ATM and `count` strikes above ATM,
        plus the ATM strike itself. Total strikes = 2*count + 1.

        Args:
            strike_prices: List of all available strike prices (sorted)
            atm_strike: The ATM strike price
            count: Number of strikes to select ABOVE and BELOW ATM (each)

        Returns:
            list[Decimal]: Selected strike prices centered around ATM
        """
        sorted_strikes = sorted(strike_prices)

        # Find ATM index
        try:
            atm_index = sorted_strikes.index(atm_strike)
        except ValueError:
            # Fallback: find closest
            atm_index = min(
                range(len(sorted_strikes)),
                key=lambda i: abs(sorted_strikes[i] - atm_strike),
            )

        # Select `count` strikes below ATM and `count` strikes above ATM
        # Total strikes = 2*count + 1 (including ATM)
        start_index = max(0, atm_index - count)
        end_index = min(len(sorted_strikes), atm_index + count + 1)

        return sorted_strikes[start_index:end_index]

    @classmethod
    def generate_symbols_from_chain(
        cls,
        fyers_symbol: str,
        chain_data: dict,
        strike_count: int,
    ) -> dict[str, list[str] | dict | str]:
        """Generate CE/PE symbols from option chain response.

        Uses the robust parsing functions that correctly handle FYERS API response:
        - expiryData (not expiryDates)
        - optionsChain (not ceChain/peChain)
        - trading_symbol directly from API

        Strikes are selected centered around ATM - with strike_count/2 above
        and strike_count/2 below the ATM strike.

        Args:
            fyers_symbol: Base instrument symbol (e.g., NSE:NIFTY50-INDEX)
            chain_data: Raw FYERS option chain API response
            strike_count: Number of strikes to process (centered around ATM)

        Returns:
            dict: {
                "symbols": ["NSE:NIFTY24MAR22000CE", "NSE:NIFTY24MAR22000PE", ...],
                "symbol_mapping": {
                    "22000": {"CE": "NSE:NIFTY24MAR22000CE", "PE": "NSE:NIFTY24MAR22000PE"},
                    ...
                },
                "expiry_date": "2026-03-26"
            }
        """
        try:
            # Parse expiry candidates using existing robust parser
            expiry_candidates = parse_expiry_candidates(chain_data)
            if not expiry_candidates:
                raise ValueError("No expiry dates in option chain")

            # Use first expiry (nearest)
            expiry_date: date = expiry_candidates[0]["expiry_date"]

            # Parse all option rows using existing robust parser
            all_rows = parse_option_rows(chain_data)

            # Filter to first expiry only
            expiry_rows = [row for row in all_rows if row["expiry_date"] == expiry_date]

            if not expiry_rows:
                raise ValueError(
                    f"No option rows found for expiry {expiry_date} in option chain"
                )

            # Parse spot price to find ATM
            try:
                spot_price = parse_spot_price(chain_data)
            except ValueError:
                # Fallback: estimate from first LTP if spot not available
                spot_price = None
                logger.warning(
                    "Could not parse spot price, using first available strike as ATM"
                )

            # Collect all unique strikes for this expiry
            unique_strikes: set[Decimal] = set()
            for row in expiry_rows:
                strike_price = row.get("strike_price")
                if strike_price is not None:
                    unique_strikes.add(strike_price)

            if not unique_strikes:
                raise ValueError("No valid strike prices found in option chain")

            # Find ATM strike
            if spot_price is not None:
                atm_strike = cls._find_atm_strike(list(unique_strikes), spot_price)
            else:
                # Fallback: use middle strike
                sorted_fallback = sorted(unique_strikes)
                atm_strike = sorted_fallback[len(sorted_fallback) // 2]

            # Select strikes centered around ATM
            selected_strikes = cls._select_strikes_around_atm(
                list(unique_strikes), atm_strike, strike_count
            )
            selected_strike_strs = {str(int(float(s))) for s in selected_strikes}

            logger.info(
                f"Selected strikes centered around ATM - "
                f"atm_strike: {int(float(atm_strike))}, "
                f"spot_price: {float(spot_price) if spot_price else 'N/A'}, "
                f"strikes: {[int(float(s)) for s in selected_strikes]}"
            )

            # Build symbols and mapping for selected strikes only
            symbols: list[str] = []
            symbol_mapping: dict[str, dict[str, str]] = {}

            for row in expiry_rows:
                strike_price = row.get("strike_price")
                option_type = row.get("option_type")
                trading_symbol = row.get("trading_symbol")

                if not all([strike_price, option_type, trading_symbol]):
                    continue

                strike_str = str(int(float(strike_price)))

                # Only include strikes in our selected set
                if strike_str not in selected_strike_strs:
                    continue

                if strike_str not in symbol_mapping:
                    symbol_mapping[strike_str] = {}

                # Add symbol to mapping
                symbol_mapping[strike_str][option_type] = trading_symbol
                symbols.append(trading_symbol)

            logger.info(
                f"Generated option symbols - "
                f"fyers_symbol: {fyers_symbol}, "
                f"expiry: {expiry_date}, "
                f"strikes: {len(symbol_mapping)}, "
                f"total_symbols: {len(symbols)}"
            )

            return {
                "symbols": symbols,
                "symbol_mapping": symbol_mapping,
                "expiry_date": expiry_date.isoformat(),
            }

        except Exception as error:
            logger.error(
                f"Failed to generate symbols - error: {str(error)} - "
                f"fyers_symbol: {fyers_symbol}"
            )
            raise
