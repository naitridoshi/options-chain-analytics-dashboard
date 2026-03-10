from datetime import date

from libs.platform.modules.option_chain_snapshot.src import (
    parse_expiry_candidates,
    parse_option_rows,
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

        Args:
            fyers_symbol: Base instrument symbol (e.g., NSE:NIFTY50-INDEX)
            chain_data: Raw FYERS option chain API response
            strike_count: Number of strikes to process

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

            # Build symbols and mapping
            symbols: list[str] = []
            symbol_mapping: dict[str, dict[str, str]] = {}

            # Group by strike and limit to strike_count
            seen_strikes: set[str] = set()

            for row in expiry_rows:
                strike_price = row.get("strike_price")
                option_type = row.get("option_type")
                trading_symbol = row.get("trading_symbol")

                if not all([strike_price, option_type, trading_symbol]):
                    continue

                strike_str = str(int(float(strike_price)))

                # Limit to strike_count unique strikes
                if strike_str not in seen_strikes:
                    if len(seen_strikes) >= strike_count:
                        continue
                    seen_strikes.add(strike_str)
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
