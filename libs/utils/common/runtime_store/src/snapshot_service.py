from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from libs.platform.modules.option_chain_snapshot.src import (
    normalize_interval_boundary,
)
from libs.platform.modules.option_chain_snapshot.src.runtime_summary import (
    build_summary_payloads,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.config.src.fyers import SNAPSHOT_INTERVAL_SECONDS
from libs.utils.db.redis.src import RedisOptionChainSnapshotStore

IST = ZoneInfo("Asia/Kolkata")

log = CustomLogger("RuntimeSnapshotPayload")
logger, listener = log.get_logger()
listener.start()


@dataclass
class RuntimeSnapshotPayload:
    instrument: dict
    market_date: str
    refresh_seconds: int
    latest: dict
    strikes: list[dict]
    selection: str | None = None

    def to_dict(self) -> dict:
        payload = {
            "instrument": self.instrument,
            "market_date": self.market_date,
            "refresh_seconds": self.refresh_seconds,
            "latest": self.latest,
            "strikes": self.strikes,
        }
        if self.selection:
            payload["selection"] = self.selection
        return payload


class RuntimeSnapshotService:
    @staticmethod
    async def save_intraday_snapshot(
        *,
        instrument,
        captured_at: datetime,
        spot_price: Decimal,
        strike_rows: list[dict],
    ) -> None:
        payload = RuntimeSnapshotService.build_runtime_payload(
            instrument=instrument,
            captured_at=captured_at,
            spot_price=spot_price,
            strike_rows=strike_rows,
        )
        await RedisOptionChainSnapshotStore.save_intraday_snapshot(
            instrument_symbol=instrument.symbol,
            trade_date=payload.market_date,
            interval_ts=payload.latest["captured_at"],
            payload=payload.to_dict(),
        )

    @staticmethod
    async def save_previous_day_final_snapshot(
        *,
        instrument,
        captured_at: datetime,
        spot_price: Decimal,
        strike_rows: list[dict],
        selection: str,
    ) -> None:
        payload = RuntimeSnapshotService.build_runtime_payload(
            instrument=instrument,
            captured_at=captured_at,
            spot_price=spot_price,
            strike_rows=strike_rows,
        )
        payload.selection = selection
        await RedisOptionChainSnapshotStore.save_previous_day_final_snapshot(
            instrument_symbol=instrument.symbol,
            payload=payload.to_dict(),
        )

    @staticmethod
    async def get_latest_captured_at_for_today_ist() -> datetime | None:
        trade_date = datetime.now(IST).date().isoformat()
        latest_captured_at: datetime | None = None

        for instrument in InstrumentCatalogService.get_active_instruments():
            latest_snapshot = await RedisOptionChainSnapshotStore.get_latest_snapshot(
                instrument_symbol=instrument.symbol,
                trade_date=trade_date,
            )
            if not latest_snapshot:
                continue
            latest = latest_snapshot.get("latest") or {}
            captured_at = latest.get("captured_at")
            if not captured_at:
                continue
            captured_at_dt = datetime.fromisoformat(captured_at)
            if latest_captured_at is None or captured_at_dt > latest_captured_at:
                latest_captured_at = captured_at_dt

        return latest_captured_at

    @staticmethod
    def build_runtime_payload(
        *,
        instrument,
        captured_at: datetime,
        spot_price: Decimal,
        strike_rows: list[dict],
    ) -> RuntimeSnapshotPayload:
        interval_summary_values, strike_summary_values = build_summary_payloads(
            strike_rows
        )

        # CRITICAL: Sort strikes by strike_price for correct PCR calculations
        # PCR functions rely on index positions where:
        # - Higher index = higher strike (CALL strikes above ATM)
        # - Lower index = lower strike (PUT strikes below ATM)
        strike_summary_values = sorted(
            strike_summary_values, key=lambda x: x["strike_price"]
        )

        latest = {
            "captured_at": captured_at.isoformat(),
            "spot_price": _as_number(spot_price),
            "call_oi_change_sum": interval_summary_values["call_oi_change_sum"],
            "put_oi_change_sum": interval_summary_values["put_oi_change_sum"],
            "net_oi_change_sum": interval_summary_values["net_oi_change_sum"],
            "call_oi_sum": interval_summary_values["call_oi_sum"],
            "put_oi_sum": interval_summary_values["put_oi_sum"],
            "net_oi_sum": interval_summary_values["net_oi_sum"],
            "pcr_oi": _as_number(interval_summary_values["pcr_oi"]),
            "pcr_oi_change": _as_number(interval_summary_values["pcr_oi_change"]),
            "call_oi_share_pct": _as_number(
                interval_summary_values["call_oi_share_pct"]
            ),
            "put_oi_share_pct": _as_number(interval_summary_values["put_oi_share_pct"]),
            "call_oi_change_share_pct": _as_number(
                interval_summary_values["call_oi_change_share_pct"]
            ),
            "put_oi_change_share_pct": _as_number(
                interval_summary_values["put_oi_change_share_pct"]
            ),
            "coi_pcr_window": _compute_window_pcr(strike_summary_values, spot_price, 6),
            "atm_pcr": _compute_atm_pcr(strike_summary_values, spot_price),
            "strength_pcr": _compute_strength_pcr(strike_summary_values, spot_price),
        }

        logger.info(
            f"PCR Values Calculated - spot_price: {spot_price} - "
            f"COI_PCR_Window: {latest['coi_pcr_window']} - "
            f"ATM_PCR: {latest['atm_pcr']} - "
            f"Strength_PCR: {latest['strength_pcr']} - "
            f"strikes_count: {len(strike_summary_values)}"
        )
        strikes = []
        for item in strike_summary_values:
            strike_price = _as_number(item["strike_price"])
            call_symbol = item.get("call_trading_symbol")
            put_symbol = item.get("put_trading_symbol")

            # CRITICAL: Validate that CALL and PUT symbols are different
            if call_symbol and put_symbol and call_symbol == put_symbol:
                # This is a critical data error - skip this strike
                import logging

                logging.getLogger(__name__).error(
                    f"CRITICAL DATA ERROR in build_runtime_payload: "
                    f"CALL and PUT have same trading symbol at strike {strike_price}: "
                    f"symbol={call_symbol}. Skipping this strike to prevent LTP contamination."
                )
                continue

            strikes.append(
                {
                    "strike_price": strike_price,
                    "call_trading_symbol": call_symbol,
                    "put_trading_symbol": put_symbol,
                    "call_oi_change": item["call_oi_change"],
                    "put_oi_change": item["put_oi_change"],
                    "net_oi_change": item["net_oi_change"],
                    "call_oi": item["call_oi"],
                    "put_oi": item["put_oi"],
                    "net_oi": item["net_oi"],
                    "call_volume": item["call_volume"],
                    "put_volume": item["put_volume"],
                    "call_ltp": _as_number(item["call_ltp"]),
                    "call_ltp_change": _as_number(item["call_ltp_change"]),
                    "put_ltp": _as_number(item["put_ltp"]),
                    "put_ltp_change": _as_number(item["put_ltp_change"]),
                }
            )
        normalized = normalize_interval_boundary(captured_at)
        market_date = normalized.astimezone(IST).date().isoformat()

        return RuntimeSnapshotPayload(
            instrument={
                "id": str(getattr(instrument, "id", instrument.symbol)),
                "symbol": instrument.symbol,
                "name": getattr(instrument, "name", None) or instrument.symbol,
                "exchange": getattr(instrument, "exchange", None),
                "instrument_type": getattr(instrument, "instrument_type", None),
                "fyers_symbol": instrument.fyers_symbol,
            },
            market_date=market_date,
            refresh_seconds=SNAPSHOT_INTERVAL_SECONDS,
            latest=latest,
            strikes=strikes,
        )


def _as_number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _pcr(put_total: int, call_total: int):
    if call_total == 0:
        if put_total == 0:
            return None
        return "INF" if put_total > 0 else "-INF"
    return put_total / call_total


def _closest_atm_index(rows: list[dict], spot_price: Decimal) -> int | None:
    if not rows:
        return None
    spot = float(spot_price)
    return min(
        range(len(rows)),
        key=lambda idx: abs(float(rows[idx]["strike_price"]) - spot),
    )


def _sum_range(rows: list[dict], start_idx: int, end_idx: int, field_name: str) -> int:
    start = max(0, start_idx)
    end = min(len(rows) - 1, end_idx)
    if start > end:
        return 0
    total = 0
    for idx in range(start, end + 1):
        total += int(rows[idx].get(field_name) or 0)
    return total


def _compute_window_pcr(rows: list[dict], spot_price: Decimal, window: int):
    atm_index = _closest_atm_index(rows, spot_price)
    if atm_index is None:
        return None

    atm_strike = rows[atm_index]["strike_price"]
    call_total = _sum_range(
        rows, atm_index - window, atm_index + window, "call_oi_change"
    )
    put_total = _sum_range(
        rows, atm_index - window, atm_index + window, "put_oi_change"
    )

    pcr_value = _pcr(put_total, call_total)

    # Debug logging for COI PCR Window calculation
    logger.debug(
        f"COI PCR Window Calculation - spot_price: {spot_price} - "
        f"atm_index: {atm_index} - atm_strike: {atm_strike} - "
        f"window: {window} (strikes {atm_index - window} to {atm_index + window}) - "
        f"call_total: {call_total} - put_total: {put_total} - "
        f"COI_PCR: {pcr_value}"
    )

    return pcr_value


def _compute_atm_pcr(rows: list[dict], spot_price: Decimal):
    """
    ATM PCR = PUT COI at (ATM + 1 strike BELOW) / CALL COI at (ATM + 1 strike ABOVE)
    Formula: PUT at strike below ATM divided by CALL at strike above ATM
    Example: If ATM = 24600, then PUT at 24550 / CALL at 24650
    """
    atm_index = _closest_atm_index(rows, spot_price)
    if atm_index is None:
        return None

    # Log ATM strike for debugging
    atm_strike = rows[atm_index]["strike_price"]
    call_strike = (
        rows[atm_index + 1]["strike_price"] if atm_index + 1 < len(rows) else None
    )
    put_strike = rows[atm_index - 1]["strike_price"] if atm_index - 1 >= 0 else None

    # CALL: strike ABOVE ATM (higher strike, higher index)
    call_total = _sum_range(rows, atm_index + 1, atm_index + 1, "call_oi_change")
    # PUT: strike BELOW ATM (lower strike, lower index)
    put_total = _sum_range(rows, atm_index - 1, atm_index - 1, "put_oi_change")

    pcr_value = _pcr(put_total, call_total)

    # Debug logging for ATM PCR calculation
    logger.debug(
        f"ATM PCR Calculation - spot_price: {spot_price} - "
        f"atm_index: {atm_index} - atm_strike: {atm_strike} - "
        f"call_strike: {call_strike} (COI: {call_total}) - "
        f"put_strike: {put_strike} (COI: {put_total}) - "
        f"ATM_PCR: {pcr_value}"
    )

    return pcr_value


def _compute_strength_pcr(rows: list[dict], spot_price: Decimal):
    """
    Strength PCR = CALL COI of (ATM + 4 strikes ABOVE) / PUT COI of (ATM + 4 strikes BELOW)
    Formula: Sum of CALL OI changes from ATM to 4 strikes above,
             divided by sum of PUT OI changes from ATM to 4 strikes below.
    Example: If ATM = 24600,
             CALL: 24600, 24650, 24700, 24750, 24800 (indices N to N+4)
             PUT: 24600, 24550, 24500, 24450, 24400 (indices N-4 to N)
    """
    atm_index = _closest_atm_index(rows, spot_price)
    if atm_index is None:
        return None

    atm_strike = rows[atm_index]["strike_price"]
    # CALL: ATM and 4 strikes ABOVE (higher strikes, higher indices)
    call_total = _sum_range(rows, atm_index, atm_index + 4, "call_oi_change")
    # PUT: ATM and 4 strikes BELOW (lower strikes, lower indices)
    put_total = _sum_range(rows, atm_index - 4, atm_index, "put_oi_change")

    pcr_value = _pcr(put_total, call_total)

    # Debug logging for Strength PCR calculation
    logger.debug(
        f"Strength PCR Calculation - spot_price: {spot_price} - "
        f"atm_index: {atm_index} - atm_strike: {atm_strike} - "
        f"call_range: [{atm_index} to {atm_index + 4}] - "
        f"put_range: [{atm_index - 4} to {atm_index}] - "
        f"call_total: {call_total} - put_total: {put_total} - "
        f"Strength_PCR: {pcr_value}"
    )

    return pcr_value
