from dataclasses import dataclass, field

from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("SymbolMappingService")
logger, listener = log.get_logger()
listener.start()


@dataclass
class SymbolMapping:
    """Represents the symbol mapping for a given expiry."""

    instrument_symbol: str
    expiry_date: str
    symbol_to_strike: dict[str, str] = field(default_factory=dict)  # {symbol: strike}
    strike_to_symbols: dict[str, dict] = field(
        default_factory=dict
    )  # {strike: {CE, PE}}
    all_symbols: list[str] = field(default_factory=list)  # all symbols

    def get_strike_for_symbol(self, symbol: str) -> str | None:
        """Get strike for a symbol."""
        return self.symbol_to_strike.get(symbol)

    def get_symbols_for_strike(self, strike: str) -> dict | None:
        """Get CE/PE symbols for a strike."""
        return self.strike_to_symbols.get(strike)

    def has_symbol(self, symbol: str) -> bool:
        """Check if symbol exists in mapping."""
        return symbol in self.symbol_to_strike

    def get_all_symbols(self) -> list[str]:
        """Get all symbols."""
        return self.all_symbols.copy()


class SymbolMappingService:
    """Manages symbol-to-strike mappings and vice versa."""

    def __init__(self):
        self._current_mapping: SymbolMapping | None = None

    def update_mapping(
        self,
        instrument_symbol: str,
        expiry_date: str,
        symbol_mapping: dict[str, dict],
        all_symbols: list[str],
    ) -> SymbolMapping:
        """Update the symbol mapping.

        Args:
            instrument_symbol: Base instrument (e.g., NIFTY)
            expiry_date: Expiry date
            symbol_mapping: {strike: {CE, PE}} mapping from symbol generator
            all_symbols: List of all symbols

        Returns:
            SymbolMapping: Updated mapping object
        """
        try:
            symbol_to_strike = {}
            strike_to_symbols = {}

            # Reverse mapping: symbol -> strike
            for strike, option_symbols in symbol_mapping.items():
                for option_type, symbol in option_symbols.items():
                    symbol_to_strike[symbol] = strike
                strike_to_symbols[strike] = option_symbols

            self._current_mapping = SymbolMapping(
                instrument_symbol=instrument_symbol,
                expiry_date=expiry_date,
                symbol_to_strike=symbol_to_strike,
                strike_to_symbols=strike_to_symbols,
                all_symbols=all_symbols,
            )

            logger.info(
                f"Symbol mapping updated - "
                f"instrument: {instrument_symbol}, "
                f"expiry: {expiry_date}, "
                f"strikes: {len(strike_to_symbols)}, "
                f"symbols: {len(all_symbols)}"
            )

            return self._current_mapping

        except Exception as error:
            logger.error(f"Failed to update symbol mapping - error: {str(error)}")
            raise

    def get_current_mapping(self) -> SymbolMapping | None:
        """Get current active mapping."""
        return self._current_mapping

    def get_strike_for_symbol(self, symbol: str) -> str | None:
        """Get strike for a symbol."""
        if not self._current_mapping:
            return None
        return self._current_mapping.get_strike_for_symbol(symbol)

    def get_symbols_for_strike(self, strike: str) -> dict | None:
        """Get CE/PE symbols for a strike."""
        if not self._current_mapping:
            return None
        return self._current_mapping.get_symbols_for_strike(strike)

    def is_symbol_valid(self, symbol: str) -> bool:
        """Check if symbol is in current mapping."""
        if not self._current_mapping:
            return False
        return self._current_mapping.has_symbol(symbol)

    def get_all_symbols(self) -> list[str]:
        """Get all symbols in current mapping."""
        if not self._current_mapping:
            return []
        return self._current_mapping.get_all_symbols()

    def has_active_mapping(self) -> bool:
        """Check if there's an active mapping."""
        return self._current_mapping is not None

    def clear_mapping(self) -> None:
        """Clear current mapping (useful before refresh)."""
        self._current_mapping = None
        logger.info("Symbol mapping cleared")


# Global instance
_symbol_mapping_service: SymbolMappingService | None = None


def get_symbol_mapping_service() -> SymbolMappingService:
    """Get or create the global symbol mapping service.

    Returns:
        SymbolMappingService: The global instance
    """
    global _symbol_mapping_service
    if _symbol_mapping_service is None:
        _symbol_mapping_service = SymbolMappingService()
    return _symbol_mapping_service
