"""
COI Live Service - Provides real-time COI (Change in Open Interest) data
for the COI Live page.

Calculates:
- Intraday: PUT COI - CALL COI (Change in OI difference)
- Weekly: PUT OI - CALL OI (Total OI difference)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.config.src.fyers import (
    COI_LIVE_INTERVAL_MINUTES,
    COI_LIVE_START_HOUR,
    COI_LIVE_START_MINUTE,
    COI_LIVE_STRIKE_COUNT,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
)
from libs.utils.db.redis.src import RedisOptionChainSnapshotStore

IST = ZoneInfo("Asia/Kolkata")


def _round_to_interval(dt: datetime, interval_minutes: int) -> datetime:
    """Round datetime to nearest interval boundary."""
    minutes = dt.hour * 60 + dt.minute
    rounded_minutes = round(minutes / interval_minutes) * interval_minutes
    new_hour = int(rounded_minutes // 60)
    new_minute = int(rounded_minutes % 60)
    return dt.replace(hour=new_hour, minute=new_minute, second=0, microsecond=0)


log = CustomLogger("COILiveService")
logger, listener = log.get_logger()
listener.start()


class COILiveService:
    """Service for fetching and calculating COI Live data."""

    @staticmethod
    def _get_time_slots(trade_date: date) -> list[datetime]:
        """Generate time slots from COI Live start time to market close."""
        start_dt = datetime.combine(
            trade_date,
            time(hour=COI_LIVE_START_HOUR, minute=COI_LIVE_START_MINUTE),
            tzinfo=IST,
        )
        end_dt = datetime.combine(
            trade_date,
            time(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE),
            tzinfo=IST,
        )

        slots = []
        current = start_dt
        while current <= end_dt:
            slots.append(current)
            current += timedelta(minutes=COI_LIVE_INTERVAL_MINUTES)

        return slots

    @staticmethod
    def _find_atm_strike(strike_prices: list[int], spot_price: float) -> int | None:
        """Find the ATM (At The Money) strike price."""
        if not strike_prices or spot_price is None:
            return None

        return min(strike_prices, key=lambda sp: abs(sp - spot_price))

    @staticmethod
    def _calculate_intraday(strike: dict) -> int:
        """Calculate Intraday value: PUT COI - CALL COI."""
        put_coi = strike.get("put_oi_change", 0) or 0
        call_coi = strike.get("call_oi_change", 0) or 0
        return put_coi - call_coi

    @staticmethod
    def _calculate_weekly(strike: dict) -> int:
        """Calculate Weekly value: PUT OI - CALL OI."""
        put_oi = strike.get("put_oi", 0) or 0
        call_oi = strike.get("call_oi", 0) or 0
        return put_oi - call_oi

    @classmethod
    async def get_coi_live_data(cls, symbol: str | None = None) -> dict:
        """
        Get COI Live data for the dashboard.

        Returns:
        {
            "instrument": {...},
            "market_date": "2024-01-15",
            "spot_price": 23250.50,
            "atm_strike": 23250,
            "time_slots": ["09:15", "09:20", ...],
            "strikes": [
                {
                    "strike_price": 23200,
                    "is_atm": false,
                    "data": {
                        "09:15": {"intraday": 12345, "weekly": 67890},
                        "09:20": {"intraday": 13456, "weekly": 68901},
                        ...
                    },
                    "live": {"intraday": 14567, "weekly": 70012}
                },
                ...
            ]
        }
        """
        instruments = InstrumentCatalogService.get_active_instruments()
        if not instruments:
            return {
                "instrument": None,
                "market_date": datetime.now(IST).date().isoformat(),
                "spot_price": None,
                "atm_strike": None,
                "time_slots": [],
                "strikes": [],
            }

        # Select instrument
        instrument = instruments[0]
        if symbol:
            for inst in instruments:
                if inst.symbol.upper() == symbol.upper():
                    instrument = inst
                    break

        trade_date = datetime.now(IST).date()
        time_slots = cls._get_time_slots(trade_date)

        # Get all snapshots from timeline with error handling
        # Use higher limit to ensure we get all snapshots for the day (75 slots from 9:15 to 15:30)
        try:
            timeline = await RedisOptionChainSnapshotStore.get_timeline(
                instrument_symbol=instrument.symbol,
                trade_date=trade_date.isoformat(),
                limit=200,
            )
        except Exception as e:
            logger.warning(f"Failed to get timeline from Redis: {e}")
            timeline = []

        if not timeline:
            return {
                "instrument": {
                    "id": str(getattr(instrument, "id", instrument.symbol)),
                    "symbol": instrument.symbol,
                    "name": getattr(instrument, "name", None) or instrument.symbol,
                },
                "market_date": trade_date.isoformat(),
                "spot_price": None,
                "atm_strike": None,
                "time_slots": [slot.strftime("%H:%M") for slot in time_slots],
                "strikes": [],
            }

        # Get latest snapshot for spot price
        latest_snapshot = timeline[0] if timeline else None
        spot_price = None
        if latest_snapshot:
            latest_data = latest_snapshot.get("latest", {})
            spot_price = latest_data.get("spot_price")

        # Build a map of strike_price -> {time_slot -> {intraday, weekly}}
        all_strikes_map: dict[int, dict[str, dict[str, int]]] = {}

        for snapshot in timeline:
            captured_at = snapshot.get("latest", {}).get("captured_at")
            if not captured_at:
                continue

            # Parse the timestamp and get IST time
            captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            captured_ist = captured_dt.astimezone(IST)
            # Round to nearest interval to align with predefined time slots
            rounded_ist = _round_to_interval(captured_ist, COI_LIVE_INTERVAL_MINUTES)
            time_key = rounded_ist.strftime("%H:%M")

            strikes = snapshot.get("strikes", [])
            for strike in strikes:
                strike_price = strike.get("strike_price")
                if strike_price is None:
                    continue

                if strike_price not in all_strikes_map:
                    all_strikes_map[strike_price] = {}

                intraday = cls._calculate_intraday(strike)
                weekly = cls._calculate_weekly(strike)

                all_strikes_map[strike_price][time_key] = {
                    "intraday": intraday,
                    "weekly": weekly,
                }

        if not all_strikes_map:
            return {
                "instrument": {
                    "id": str(getattr(instrument, "id", instrument.symbol)),
                    "symbol": instrument.symbol,
                    "name": getattr(instrument, "name", None) or instrument.symbol,
                },
                "market_date": trade_date.isoformat(),
                "spot_price": spot_price,
                "atm_strike": None,
                "time_slots": [slot.strftime("%H:%M") for slot in time_slots],
                "strikes": [],
            }

        # Find ATM strike
        all_strike_prices = list(all_strikes_map.keys())
        atm_strike = cls._find_atm_strike(all_strike_prices, spot_price)

        # Filter strikes around ATM
        filtered_strike_prices = []
        if atm_strike is not None:
            sorted_strike_prices = sorted(all_strike_prices)
            atm_index = (
                sorted_strike_prices.index(atm_strike)
                if atm_strike in sorted_strike_prices
                else None
            )

            if atm_index is not None:
                start_idx = max(0, atm_index - COI_LIVE_STRIKE_COUNT)
                end_idx = min(
                    len(sorted_strike_prices), atm_index + COI_LIVE_STRIKE_COUNT + 1
                )
                filtered_strike_prices = sorted_strike_prices[start_idx:end_idx]
        else:
            # Fallback: take first N strikes
            sorted_strike_prices = sorted(all_strike_prices)
            filtered_strike_prices = sorted_strike_prices[
                : COI_LIVE_STRIKE_COUNT * 2 + 1
            ]

        # Get live data (most recent values for each strike)
        latest_time_key = None
        if latest_snapshot:
            captured_at = latest_snapshot.get("latest", {}).get("captured_at")
            if captured_at:
                captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
                captured_ist = captured_dt.astimezone(IST)
                # Round to nearest interval to align with predefined time slots
                rounded_ist = _round_to_interval(
                    captured_ist, COI_LIVE_INTERVAL_MINUTES
                )
                latest_time_key = rounded_ist.strftime("%H:%M")

        # Build final strikes data with live values
        result_strikes = []
        for strike_price in filtered_strike_prices:
            is_atm = strike_price == atm_strike
            strike_time_data = all_strikes_map.get(strike_price, {})

            # Get live values from the most recent data point
            live_data = {"intraday": 0, "weekly": 0}
            if latest_time_key and latest_time_key in strike_time_data:
                live_data = strike_time_data[latest_time_key]
            elif strike_time_data:
                # Get the most recent time slot data
                sorted_times = sorted(strike_time_data.keys())
                if sorted_times:
                    live_data = strike_time_data[sorted_times[-1]]

            result_strikes.append(
                {
                    "strike_price": strike_price,
                    "is_atm": is_atm,
                    "data": strike_time_data,
                    "live": live_data,
                }
            )

        logger.info(
            f"COI Live data fetched - "
            f"instrument: {instrument.symbol} - "
            f"strikes_count: {len(result_strikes)} - "
            f"atm_strike: {atm_strike} - "
            f"spot_price: {spot_price}"
        )

        return {
            "instrument": {
                "id": str(getattr(instrument, "id", instrument.symbol)),
                "symbol": instrument.symbol,
                "name": getattr(instrument, "name", None) or instrument.symbol,
            },
            "market_date": trade_date.isoformat(),
            "spot_price": spot_price,
            "atm_strike": atm_strike,
            "time_slots": [slot.strftime("%H:%M") for slot in time_slots],
            "strikes": result_strikes,
        }
