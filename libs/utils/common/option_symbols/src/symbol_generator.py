from datetime import datetime
from decimal import Decimal

from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("OptionSymbolGenerator")
logger, listener = log.get_logger()
listener.start()


class OptionSymbolGenerator:
    """Generates FYERS option chain symbols from raw option chain data."""

    @classmethod
    def generate_symbols_from_chain(
        cls,
        fyers_symbol: str,
        chain_data: dict,
        strike_count: int,
    ) -> dict[str, list[str]]:
        """Generate CE/PE symbols from option chain response.

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
        symbols = []
        symbol_mapping = {}
        expiry_date = None

        try:
            # Extract chain data
            data = chain_data.get("d") or chain_data.get("data") or {}
            if not isinstance(data, dict):
                raise ValueError("Invalid option chain data structure")

            # Get expiry candidates
            expiry_list = data.get("expiryDates") or []
            if not expiry_list:
                raise ValueError("No expiry dates in option chain")

            # Use first expiry (nearest)
            expiry_date = expiry_list[0]

            # Get CE/PE chain data
            ce_chain = data.get("ceChain") or []
            data.get("peChain") or []

            # Extract base symbol (remove exchange prefix)
            base_symbol = fyers_symbol.split(":")[-1]

            # Process strikes
            processed = 0
            for ce_strike in ce_chain:
                if processed >= strike_count:
                    break

                strike_price = ce_strike.get("strikePrice")
                if strike_price is None:
                    continue

                strike_str = cls._format_strike(strike_price)

                # Generate CE and PE symbols
                ce_symbol = cls._build_option_symbol(
                    base_symbol, expiry_date, strike_str, "CE"
                )
                pe_symbol = cls._build_option_symbol(
                    base_symbol, expiry_date, strike_str, "PE"
                )

                symbols.append(ce_symbol)
                symbols.append(pe_symbol)

                symbol_mapping[strike_str] = {"CE": ce_symbol, "PE": pe_symbol}

                processed += 1

            logger.info(
                f"Generated option symbols - "
                f"base_symbol: {base_symbol}, "
                f"expiry: {expiry_date}, "
                f"strikes: {len(symbol_mapping)}, "
                f"total_symbols: {len(symbols)}"
            )

            return {
                "symbols": symbols,
                "symbol_mapping": symbol_mapping,
                "expiry_date": expiry_date,
            }

        except Exception as error:
            logger.error(
                f"Failed to generate symbols - error: {str(error)} - "
                f"fyers_symbol: {fyers_symbol}"
            )
            raise

    @classmethod
    def _build_option_symbol(
        cls, base_symbol: str, expiry_date: str, strike: str, option_type: str
    ) -> str:
        """Build full option symbol in FYERS format.

        Example: NSE:NIFTY24MAR22000CE

        Args:
            base_symbol: NIFTY50, BANKNIFTY, etc.
            expiry_date: 2026-03-26
            strike: 22000
            option_type: CE or PE

        Returns:
            str: Full symbol in FYERS format
        """
        expiry_str = cls._format_expiry_for_symbol(expiry_date)
        return f"NSE:{base_symbol}{expiry_str}{strike}{option_type}"

    @classmethod
    def _format_expiry_for_symbol(cls, expiry_date: str) -> str:
        """Convert ISO date to FYERS symbol format (e.g., 24MAR).

        Args:
            expiry_date: ISO format date string (2026-03-26)

        Returns:
            str: Formatted expiry (24MAR)
        """
        try:
            date_obj = datetime.fromisoformat(expiry_date)
            day = date_obj.strftime("%d").lstrip("0")  # Remove leading zero
            month = date_obj.strftime("%b").upper()
            year = date_obj.strftime("%y")
            return f"{year}{month}{day}"
        except Exception as error:
            logger.error(f"Failed to format expiry date - error: {str(error)}")
            raise

    @classmethod
    def _format_strike(cls, strike_price: float | Decimal) -> str:
        """Format strike price as integer string.

        Args:
            strike_price: Strike price (22000.0 or Decimal('22000'))

        Returns:
            str: Formatted strike (22000)
        """
        return str(int(float(strike_price)))
