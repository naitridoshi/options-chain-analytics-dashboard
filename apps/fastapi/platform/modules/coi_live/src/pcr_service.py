"""
COI PCR Live Service - Provides real-time PCR (Put-Call Ratio) percentage data
for the COI PCR Live page.

Calculates:
- Call% = (Call COI / (Call COI + Put COI)) * 100
- Put% = (Put COI / (Call COI + Put COI)) * 100
- COI PCR = Put COI / Call COI
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
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
)
from libs.utils.db.redis.src import RedisOptionChainSnapshotStore

IST = ZoneInfo("Asia/Kolkata")

log = CustomLogger("COIPCRLiveService")
logger, listener = log.get_logger()
listener.start()


class COIPCRLiveService:
    """Service for fetching and calculating COI PCR Live data."""

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
    def _calculate_call_pct(call_coi: int, put_coi: int) -> float | None:
        """Calculate Call% = (Call COI / (Call COI + Put COI)) * 100."""
        total = call_coi + put_coi
        if total == 0:
            return None
        return (call_coi / total) * 100

    @staticmethod
    def _calculate_put_pct(call_coi: int, put_coi: int) -> float | None:
        """Calculate Put% = (Put COI / (Call COI + Put COI)) * 100."""
        total = call_coi + put_coi
        if total == 0:
            return None
        return (put_coi / total) * 100

    @staticmethod
    def _calculate_pcr(call_coi: int, put_coi: int) -> float | str | None:
        """Calculate COI PCR = Put COI / Call COI."""
        if call_coi == 0:
            if put_coi == 0:
                return None
            return "INF" if put_coi > 0 else "-INF"
        return put_coi / call_coi

    @classmethod
    async def get_coi_pcr_live_data(cls, symbol: str | None = None) -> dict:
        """
        Get COI PCR Live data for the dashboard.

        Returns:
        {
            "instrument": {...},
            "market_date": "2024-01-15",
            "current": {
                "call_pct": 55.5,
                "put_pct": 44.5,
                "coi_pcr": 0.80
            },
            "time_slots": ["09:15", "09:20", ...],
            "data": [
                {
                    "time": "09:15",
                    "call_pct": 55.5,
                    "put_pct": 44.5,
                    "coi_pcr": 0.80,
                    "dominance": "call"  # "call" if call_pct > 50, "put" if put_pct > 50
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
                "current": None,
                "time_slots": [],
                "data": [],
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
        try:
            timeline = await RedisOptionChainSnapshotStore.get_timeline(
                instrument_symbol=instrument.symbol,
                trade_date=trade_date.isoformat(),
                limit=100,
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
                "current": None,
                "time_slots": [slot.strftime("%H:%M") for slot in time_slots],
                "data": [],
            }

        # Process timeline data
        time_data_map: dict[str, dict] = {}

        for snapshot in timeline:
            captured_at = snapshot.get("latest", {}).get("captured_at")
            if not captured_at:
                continue

            # Parse the timestamp and get IST time
            captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            captured_ist = captured_dt.astimezone(IST)
            time_key = captured_ist.strftime("%H:%M")

            # Get aggregated COI values from latest section
            latest = snapshot.get("latest", {})
            call_coi_sum = latest.get("call_oi_change_sum", 0) or 0
            put_coi_sum = latest.get("put_oi_change_sum", 0) or 0

            # Calculate percentages and PCR
            call_pct = cls._calculate_call_pct(call_coi_sum, put_coi_sum)
            put_pct = cls._calculate_put_pct(call_coi_sum, put_coi_sum)
            coi_pcr = cls._calculate_pcr(call_coi_sum, put_coi_sum)

            # Determine dominance
            dominance = "neutral"
            if call_pct is not None and put_pct is not None:
                if call_pct > 50:
                    dominance = "call"
                elif put_pct > 50:
                    dominance = "put"

            time_data_map[time_key] = {
                "time": time_key,
                "call_pct": call_pct,
                "put_pct": put_pct,
                "coi_pcr": coi_pcr,
                "dominance": dominance,
            }

        # Build data array for all time slots
        data = []
        current_data = None
        prev_dominance = None

        for slot in time_slots:
            time_key = slot.strftime("%H:%M")
            slot_data = time_data_map.get(time_key)

            if slot_data:
                # Track dominance shifts
                if (
                    prev_dominance is not None
                    and slot_data["dominance"] != prev_dominance
                ):
                    slot_data["shift"] = True
                else:
                    slot_data["shift"] = False

                prev_dominance = slot_data["dominance"]
                current_data = slot_data
                data.append(slot_data)
            else:
                # Fill empty slot
                data.append(
                    {
                        "time": time_key,
                        "call_pct": None,
                        "put_pct": None,
                        "coi_pcr": None,
                        "dominance": "neutral",
                        "shift": False,
                    }
                )

        # Get current values (most recent)
        current = None
        if current_data:
            current = {
                "call_pct": current_data.get("call_pct"),
                "put_pct": current_data.get("put_pct"),
                "coi_pcr": current_data.get("coi_pcr"),
                "dominance": current_data.get("dominance"),
            }

        logger.info(
            f"COI PCR Live data fetched - "
            f"instrument: {instrument.symbol} - "
            f"data_points: {len(data)} - "
            f"current_dominance: {current.get('dominance') if current else 'N/A'}"
        )

        return {
            "instrument": {
                "id": str(getattr(instrument, "id", instrument.symbol)),
                "symbol": instrument.symbol,
                "name": getattr(instrument, "name", None) or instrument.symbol,
            },
            "market_date": trade_date.isoformat(),
            "current": current,
            "time_slots": [slot.strftime("%H:%M") for slot in time_slots],
            "data": data,
        }
