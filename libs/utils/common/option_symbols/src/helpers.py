from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("SymbolMappingHelpers")
logger, listener = log.get_logger()
listener.start()


def build_symbol_to_strike_mapping(
    symbol_mapping: dict[str, dict[str, str]],
) -> tuple[dict[str, str], str | None]:
    """Build symbol->strike lookup from symbol_mapping.

    Takes a symbol_mapping like:
        {
            "22000": {"CE": "NSE:NIFTY24MAR22000CE", "PE": "NSE:NIFTY24MAR22000PE"},
            "22100": {"CE": "NSE:NIFTY24MAR22100CE", "PE": "NSE:NIFTY24MAR22100PE"},
        }

    And returns:
        (
            {
                "NSE:NIFTY24MAR22000CE": "22000",
                "NSE:NIFTY24MAR22000PE": "22000",
                "NSE:NIFTY24MAR22100CE": "22100",
                "NSE:NIFTY24MAR22100PE": "22100",
            },
            expiry_date  # extracted from first symbol if possible
        )

    Args:
        symbol_mapping: Mapping of strike -> {option_type: symbol}

    Returns:
        tuple: (symbol_to_strike dict, expiry_date or None)
    """
    symbol_to_strike: dict[str, str] = {}

    for strike, symbols_dict in symbol_mapping.items():
        for option_type, symbol in symbols_dict.items():
            symbol_to_strike[symbol] = strike

    return symbol_to_strike, None


__all__ = ["build_symbol_to_strike_mapping"]
