from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from libs.utils.config.src.fyers import (
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    SNAPSHOT_INTERVAL_SECONDS,
)

IST = ZoneInfo("Asia/Kolkata")


def is_market_open_now(now_utc: datetime | None = None) -> bool:
    now_utc = now_utc or datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)
    if now_ist.weekday() > 4:
        return False

    market_open = time(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE)
    market_close = time(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE)
    return market_open <= now_ist.time() <= market_close


def normalize_interval_boundary(
    dt_utc: datetime,
    interval_seconds: int = SNAPSHOT_INTERVAL_SECONDS,
) -> datetime:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than 0")
    dt_ist = dt_utc.astimezone(IST)
    start_of_day_ist = dt_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_seconds = int((dt_ist - start_of_day_ist).total_seconds())
    second_bucket = (elapsed_seconds // interval_seconds) * interval_seconds
    normalized_ist = start_of_day_ist + timedelta(seconds=second_bucket)
    return normalized_ist.astimezone(timezone.utc)


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _to_date(value: Any) -> date | None:
    if value in (None, ""):
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10_000_000_000:
            ts = ts // 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()

    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return _to_date(int(stripped))
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d%b%y", "%d%b%Y"):
            try:
                return datetime.strptime(stripped, fmt).date()
            except ValueError:
                continue

    return None


def _get_chain_rows(data: dict) -> list[dict] | None:
    chain = data.get("optionsChain") or data.get("options_chain") or data.get("chain")
    return chain if isinstance(chain, list) else None


def parse_spot_price(payload: dict) -> Decimal:
    data = payload.get("d") or payload.get("data") or payload

    # ------------------------------------------------------------------
    # 1️⃣ Direct spot fields (some APIs return it directly)
    # ------------------------------------------------------------------
    spot = (
        data.get("ltp")
        or data.get("spot_price")
        or data.get("spotPrice")
        or data.get("underlying_value")
        or data.get("underlyingValue")
    )

    if spot is not None:
        return Decimal(str(spot))

    # ------------------------------------------------------------------
    # 2️⃣ Underlying object fallback
    # ------------------------------------------------------------------
    underlying = data.get("underlying")
    if isinstance(underlying, dict):
        spot = underlying.get("ltp") or underlying.get("last_price")
        if spot is not None:
            return Decimal(str(spot))

    # ------------------------------------------------------------------
    # 3️⃣ FYERS optionchain structure
    # underlying row = option_type "" and strike_price -1
    # Usually the first row
    # ------------------------------------------------------------------
    chain = _get_chain_rows(data)
    if chain:
        # Fast path: check first row
        first = chain[0]
        if (
            isinstance(first, dict)
            and (first.get("option_type") or "") == ""
            and first.get("strike_price") == -1
        ):
            spot = first.get("ltp") or first.get("last_price")
            if spot is not None:
                return Decimal(str(spot))

        # Fallback: scan entire chain
        for item in chain:
            if not isinstance(item, dict):
                continue

            option_type = item.get("option_type") or ""
            strike_price = item.get("strike_price")

            if option_type == "" and strike_price in (-1, None):
                spot = item.get("ltp") or item.get("last_price")
                if spot is not None:
                    return Decimal(str(spot))

    # ------------------------------------------------------------------
    # 4️⃣ If nothing found → error
    # ------------------------------------------------------------------
    raise ValueError(f"Unable to parse spot price from FYERS response: {payload}")


def parse_expiry_candidates(payload: dict) -> list[dict]:
    data = payload.get("d") or payload.get("data") or payload

    raw_candidates = (
        data.get("expiryData") or data.get("expiry_data") or data.get("expiry") or []
    )

    candidates: list[dict] = []

    for item in raw_candidates:
        if isinstance(item, dict):
            expiry_date = _to_date(
                item.get("date")
                or item.get("expiry")
                or item.get("expiry_date")
                or item.get("expiryDate")
                or item.get("expiry_dt")
                or item.get("expiry_dt_str")
                or item.get("display")
            )

            timestamp = item.get("timestamp") or item.get("expiry_ts")

            if timestamp is None:
                raw_expiry = item.get("expiry")
                if isinstance(raw_expiry, (int, float)):
                    timestamp = int(raw_expiry)
                elif isinstance(raw_expiry, str) and raw_expiry.strip().isdigit():
                    timestamp = int(raw_expiry.strip())

            if timestamp is not None:
                timestamp = int(timestamp)

            if not expiry_date and timestamp is not None:
                expiry_date = _to_date(timestamp)

            if not expiry_date:
                continue

            candidates.append(
                {
                    "expiry_date": expiry_date,
                    "timestamp": timestamp,
                    "is_weekly": bool(item.get("isWeekly", True)),
                }
            )

        else:
            expiry_date = _to_date(item)

            if expiry_date:
                candidates.append(
                    {
                        "expiry_date": expiry_date,
                        "timestamp": int(item)
                        if isinstance(item, (int, float))
                        else None,
                        "is_weekly": True,
                    }
                )

    unique = {}

    for item in sorted(candidates, key=lambda x: x["expiry_date"]):
        unique[item["expiry_date"]] = item

    return list(unique.values())


def parse_option_rows(payload: dict) -> list[dict]:
    data = payload.get("d") or payload.get("data") or payload
    chain = _get_chain_rows(data)

    fallback_expiry_date = None
    parsed_candidates = parse_expiry_candidates(payload)

    if parsed_candidates:
        fallback_expiry_date = parsed_candidates[0]["expiry_date"]

    if not isinstance(chain, list):
        return []

    rows: list[dict] = []

    for item in chain:
        if not isinstance(item, dict):
            continue

        strike_price = _to_decimal(
            item.get("strike_price") or item.get("strikePrice") or item.get("strike")
        )

        expiry_date = _to_date(
            item.get("expiry")
            or item.get("expiry_date")
            or item.get("expiryDate")
            or item.get("expiry_dt")
            or item.get("expiry_ts")
        )

        # ------------------------------------------------------------------
        # Handle nested CE/PE structures (other broker formats)
        # ------------------------------------------------------------------
        for side_name, option_type in (
            ("ce", "CE"),
            ("pe", "PE"),
            ("CE", "CE"),
            ("PE", "PE"),
        ):
            contract = item.get(side_name)

            if not isinstance(contract, dict):
                continue

            trading_symbol = (
                contract.get("symbol")
                or contract.get("trading_symbol")
                or contract.get("tradingSymbol")
            )

            if not trading_symbol or strike_price is None:
                continue

            row_expiry_date = expiry_date or _to_date(
                contract.get("expiry")
                or contract.get("expiryDate")
                or contract.get("expiry_date")
                or contract.get("expiry_ts")
            )

            if row_expiry_date is None:
                row_expiry_date = fallback_expiry_date

            if row_expiry_date is None:
                continue

            rows.append(
                {
                    "expiry_date": row_expiry_date,
                    "is_weekly": bool(contract.get("isWeekly", True)),
                    "strike_price": strike_price,
                    "option_type": option_type,
                    "trading_symbol": trading_symbol,
                    "lot_size": _to_int(
                        contract.get("lot_size") or contract.get("lotSize")
                    ),
                    "ltp": _to_decimal(
                        contract.get("ltp")
                        or contract.get("last_price")
                        or contract.get("lastPrice")
                    ),
                    "ltp_change": _to_decimal(
                        contract.get("ltp_change")
                        or contract.get("ltpch")
                        or contract.get("ltpChange")
                    ),
                    "volume": _to_int(contract.get("volume") or contract.get("vol")),
                    "open_interest": _to_int(
                        contract.get("open_interest") or contract.get("oi")
                    ),
                    "oi_change": _to_int(
                        contract.get("oi_change")
                        or contract.get("oich")
                        or contract.get("oiChange")
                    ),
                    "implied_volatility": _to_decimal(
                        contract.get("implied_volatility")
                        or contract.get("iv")
                        or contract.get("impliedVolatility")
                    ),
                    "bid_price": _to_decimal(
                        contract.get("bid_price")
                        or contract.get("bid")
                        or contract.get("best_bid_price")
                    ),
                    "bid_qty": _to_int(
                        contract.get("bid_qty")
                        or contract.get("bidQty")
                        or contract.get("best_bid_qty")
                    ),
                    "ask_price": _to_decimal(
                        contract.get("ask_price")
                        or contract.get("ask")
                        or contract.get("best_ask_price")
                    ),
                    "ask_qty": _to_int(
                        contract.get("ask_qty")
                        or contract.get("askQty")
                        or contract.get("best_ask_qty")
                    ),
                }
            )

        # ------------------------------------------------------------------
        # FYERS flat optionchain rows
        # ------------------------------------------------------------------
        if item.get("option_type") and item.get("symbol") and strike_price is not None:
            row_expiry = expiry_date or _to_date(item.get("expiry_ts"))

            if row_expiry is None:
                row_expiry = fallback_expiry_date

            if row_expiry is None:
                continue

            rows.append(
                {
                    "expiry_date": row_expiry,
                    "is_weekly": bool(item.get("isWeekly", True)),
                    "strike_price": strike_price,
                    "option_type": str(item.get("option_type")).upper(),
                    "trading_symbol": item.get("symbol"),
                    "lot_size": _to_int(item.get("lot_size") or item.get("lotSize")),
                    "ltp": _to_decimal(item.get("ltp") or item.get("last_price")),
                    "ltp_change": _to_decimal(
                        item.get("ltp_change")
                        or item.get("ltpch")
                        or item.get("ltpChange")
                    ),
                    "volume": _to_int(item.get("volume") or item.get("vol")),
                    "open_interest": _to_int(
                        item.get("open_interest") or item.get("oi")
                    ),
                    "oi_change": _to_int(item.get("oi_change") or item.get("oich")),
                    "implied_volatility": _to_decimal(
                        item.get("implied_volatility") or item.get("iv")
                    ),
                    "bid_price": _to_decimal(item.get("bid_price") or item.get("bid")),
                    "bid_qty": _to_int(item.get("bid_qty") or item.get("bidQty")),
                    "ask_price": _to_decimal(item.get("ask_price") or item.get("ask")),
                    "ask_qty": _to_int(item.get("ask_qty") or item.get("askQty")),
                }
            )

    unique_rows = {}

    for row in rows:
        key = (
            row["expiry_date"],
            row["strike_price"],
            row["option_type"],
            row["trading_symbol"],
        )
        unique_rows[key] = row

    return list(unique_rows.values())
