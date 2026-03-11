from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("MarketStateManager")
logger, listener = log.get_logger()
listener.start()


@dataclass
class TickData:
    """Represents a single market tick for a symbol."""

    symbol: str
    ltp: Decimal | None = None
    avg_price: Decimal | None = None
    volume: int | None = None
    oi: int | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    last_update: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "ltp": float(self.ltp) if self.ltp else None,
            "avg_price": float(self.avg_price) if self.avg_price else None,
            "volume": self.volume,
            "oi": self.oi,
            "bid": float(self.bid) if self.bid else None,
            "ask": float(self.ask) if self.ask else None,
            "last_update": self.last_update.isoformat() if self.last_update else None,
        }


@dataclass
class StrikeData:
    """Represents market data for a strike (CE + PE combined)."""

    strike: str
    ce_data: TickData | None = None
    pe_data: TickData | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "strike": self.strike,
            "CE": self.ce_data.to_dict() if self.ce_data else None,
            "PE": self.pe_data.to_dict() if self.pe_data else None,
        }


class MarketStateManager:
    """Manages in-memory market state for symbols and strikes.

    Maintains two structures:
    1. symbol_state: symbol -> TickData
    2. strike_state: strike -> StrikeData (CE + PE)
    """

    def __init__(self):
        self._symbol_state: dict[str, TickData] = {}
        self._strike_state: dict[str, StrikeData] = {}
        self._symbol_to_strike: dict[str, str] = {}
        self._expiry_date: str | None = None

    def update_symbol_mapping(
        self, symbol_to_strike: dict[str, str], expiry_date: str
    ) -> None:
        """Update symbol to strike mapping.

        Args:
            symbol_to_strike: Mapping of symbols to strikes
            expiry_date: Current expiry date
        """
        self._symbol_to_strike = symbol_to_strike.copy()
        self._expiry_date = expiry_date

        # Initialize strike states
        strikes = set(symbol_to_strike.values())
        for strike in strikes:
            if strike not in self._strike_state:
                self._strike_state[strike] = StrikeData(strike=strike)

        logger.info(
            f"Symbol mapping updated - "
            f"symbols: {len(symbol_to_strike)}, "
            f"strikes: {len(self._strike_state)}, "
            f"expiry: {expiry_date}"
        )

    def update_tick(
        self,
        symbol: str,
        ltp: float | Decimal | None = None,
        avg_price: float | Decimal | None = None,
        volume: int | None = None,
        oi: int | None = None,
        bid: float | Decimal | None = None,
        ask: float | Decimal | None = None,
    ) -> None:
        """Update market data for a symbol.

        Only updates fields that are not None. This preserves existing values
        when WebSocket messages don't include all fields.

        Args:
            symbol: Option symbol
            ltp: Last traded price
            avg_price: Average traded price
            volume: Traded volume
            oi: Open interest
            bid: Bid price
            ask: Ask price
        """
        try:
            # Create or get symbol tick data
            if symbol not in self._symbol_state:
                self._symbol_state[symbol] = TickData(symbol=symbol)

            tick_data = self._symbol_state[symbol]

            # Only update fields that are not None (partial update)
            if ltp is not None:
                tick_data.ltp = Decimal(str(ltp))
            if avg_price is not None:
                tick_data.avg_price = Decimal(str(avg_price))
            if volume is not None:
                tick_data.volume = volume
            if oi is not None:
                tick_data.oi = oi
            if bid is not None:
                tick_data.bid = Decimal(str(bid))
            if ask is not None:
                tick_data.ask = Decimal(str(ask))

            tick_data.last_update = datetime.now(timezone.utc)

            # Update strike state if symbol has strike mapping
            if symbol in self._symbol_to_strike:
                strike = self._symbol_to_strike[symbol]
                if "CE" in symbol:
                    self._strike_state[strike].ce_data = tick_data
                elif "PE" in symbol:
                    self._strike_state[strike].pe_data = tick_data

        except Exception as error:
            logger.error(
                f"Failed to update tick - symbol: {symbol}, error: {str(error)}"
            )

    def get_symbol_data(self, symbol: str) -> TickData | None:
        """Get market data for a symbol.

        Args:
            symbol: Option symbol

        Returns:
            TickData or None if symbol not found
        """
        return self._symbol_state.get(symbol)

    def get_strike_data(self, strike: str) -> StrikeData | None:
        """Get combined CE/PE data for a strike.

        Args:
            strike: Strike price string

        Returns:
            StrikeData or None if strike not found
        """
        return self._strike_state.get(strike)

    def get_all_strikes(self) -> dict[str, StrikeData]:
        """Get all strike data.

        Returns:
            dict: {strike: StrikeData, ...}
        """
        return self._strike_state.copy()

    def get_all_symbols(self) -> dict[str, TickData]:
        """Get all symbol data.

        Returns:
            dict: {symbol: TickData, ...}
        """
        return self._symbol_state.copy()

    def has_symbol(self, symbol: str) -> bool:
        """Check if symbol exists in state.

        Args:
            symbol: Option symbol

        Returns:
            bool: True if symbol has been seen
        """
        return symbol in self._symbol_state

    def get_symbol_count(self) -> int:
        """Get total number of symbols in state."""
        return len(self._symbol_state)

    def get_strike_count(self) -> int:
        """Get total number of strikes in state."""
        return len(self._strike_state)

    def clear_state(self) -> None:
        """Clear all market state (useful before refresh)."""
        self._symbol_state.clear()
        self._strike_state.clear()
        self._symbol_to_strike.clear()
        self._expiry_date = None
        logger.info("Market state cleared")

    def initialize_from_option_rows(
        self,
        option_rows: list[dict],
        symbol_mapping: dict[str, dict[str, str]],
    ) -> int:
        """Initialize market state from parsed option chain rows.

        This method populates initial data for all symbols from the REST API
        option chain response. WebSocket updates will then merge with this data.

        Args:
            option_rows: List of parsed option rows from parse_option_rows()
            symbol_mapping: Mapping of strike -> {CE: symbol, PE: symbol}

        Returns:
            int: Number of symbols initialized
        """
        initialized = 0

        for row in option_rows:
            trading_symbol = row.get("trading_symbol")
            if not trading_symbol:
                continue

            # Get data from REST API response
            ltp = row.get("ltp")
            avg_price = row.get("avg_price")
            volume = row.get("volume")
            oi = row.get("open_interest")
            bid_price = row.get("bid_price")
            ask_price = row.get("ask_price")

            # Initialize tick data if symbol exists in mapping
            if trading_symbol not in self._symbol_state:
                self._symbol_state[trading_symbol] = TickData(symbol=trading_symbol)

            tick_data = self._symbol_state[trading_symbol]

            # Set initial values (these will be updated by WebSocket)
            if ltp is not None:
                tick_data.ltp = Decimal(str(ltp))
            if avg_price is not None:
                tick_data.avg_price = Decimal(str(avg_price))
            if volume is not None:
                tick_data.volume = volume
            if oi is not None:
                tick_data.oi = oi
            if bid_price is not None:
                tick_data.bid = Decimal(str(bid_price))
            if ask_price is not None:
                tick_data.ask = Decimal(str(ask_price))

            tick_data.last_update = datetime.now(timezone.utc)
            initialized += 1

            # Update strike state
            strike_str = str(int(float(row.get("strike_price", 0))))
            option_type = row.get("option_type")

            if strike_str in self._strike_state:
                if option_type == "CE":
                    self._strike_state[strike_str].ce_data = tick_data
                elif option_type == "PE":
                    self._strike_state[strike_str].pe_data = tick_data

        logger.info(
            f"Initialized market state from option chain - "
            f"symbols: {initialized}, "
            f"strikes: {len(self._strike_state)}"
        )

        return initialized

    def get_state_summary(self) -> dict:
        """Get summary of current state."""
        return {
            "symbols": len(self._symbol_state),
            "strikes": len(self._strike_state),
            "expiry_date": self._expiry_date,
        }
