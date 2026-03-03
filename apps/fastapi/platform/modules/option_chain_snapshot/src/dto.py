from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass
class OptionStrikeDTO:
    expiry_date: date
    is_weekly: bool
    strike_price: Decimal
    option_type: str
    trading_symbol: str
    lot_size: int | None
    ltp: Decimal | None
    volume: int | None
    open_interest: int | None
    oi_change: int | None
    implied_volatility: Decimal | None
    bid_price: Decimal | None
    bid_qty: int | None
    ask_price: Decimal | None
    ask_qty: int | None


@dataclass
class SnapshotRunResult:
    instrument_symbol: str
    fyers_symbol: str
    captured_at: datetime
    expiries_processed: int
    strikes_inserted: int
