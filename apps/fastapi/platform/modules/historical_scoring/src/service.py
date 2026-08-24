"""Historical Scoring Service - Calculates live and 5-minute historical scoring snapshots,

money flow metrics, and support & resistance rankings for Options Chain
Analytics.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.instrument_catalog.src import InstrumentCatalogService
from libs.utils.common.runtime_store.src.index_breadth_service import (
    RuntimeIndexSnapshotService,
)
from libs.utils.common.runtime_store.src.script_breadth_service import (
    RuntimeScriptSnapshotService,
)
from libs.utils.config.src.fyers import (
    COI_LIVE_INTERVAL_MINUTES,
    COI_LIVE_START_HOUR,
    COI_LIVE_START_MINUTE,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
)
from libs.utils.db.redis.src import (
    RedisOptionChainSnapshotStore,
)

IST = ZoneInfo("Asia/Kolkata")
log = CustomLogger("HistoricalScoringService")
logger, listener = log.get_logger()
listener.start()

# PCR scoring lookup table matching scoring.html / SCORING.xlsx
PCR_TABLE = [
    {
        "min": 1.00,
        "max": 1.25,
        "label": "NEUTRAL TO BULLISH",
        "trend": "BULLISH",
        "add": 10,
        "less": 1.00,
        "divide": 0.25,
        "into": 40,
    },
    {
        "min": 1.25,
        "max": 1.75,
        "label": "MODERATE BULLISH",
        "trend": "BULLISH",
        "add": 51,
        "less": 1.25,
        "divide": 0.50,
        "into": 25,
    },
    {
        "min": 1.75,
        "max": 2.25,
        "label": "STRONG BULLISH",
        "trend": "BULLISH",
        "add": 76,
        "less": 1.75,
        "divide": 0.50,
        "into": 15,
    },
    {
        "min": 2.25,
        "max": float("inf"),
        "label": "EXTREME BULLISH",
        "trend": "BULLISH",
        "add": 91,
        "less": 2.25,
        "divide": 0.75,
        "into": 10,
    },
    {
        "min": 0.75,
        "max": 1.00,
        "label": "NEUTRAL TO BEARISH",
        "trend": "BEARISH",
        "add": 10,
        "less": 1.00,
        "divide": 0.25,
        "into": 40,
    },
    {
        "min": 0.50,
        "max": 0.75,
        "label": "MODERATE BEARISH",
        "trend": "BEARISH",
        "add": 51,
        "less": 0.75,
        "divide": 0.25,
        "into": 30,
    },
    {
        "min": 0.25,
        "max": 0.50,
        "label": "STRONG BEARISH",
        "trend": "BEARISH",
        "add": 81,
        "less": 0.50,
        "divide": 0.25,
        "into": 10,
    },
    {
        "min": 0.00,
        "max": 0.25,
        "label": "EXTREME BEARISH",
        "trend": "BEARISH",
        "add": 91,
        "less": 0.25,
        "divide": 0.25,
        "into": 10,
    },
]


def _round_to_interval(dt: datetime, interval_minutes: int) -> datetime:
    """Round datetime to nearest interval boundary."""
    minutes = dt.hour * 60 + dt.minute
    rounded_minutes = round(minutes / interval_minutes) * interval_minutes
    new_hour = int(rounded_minutes // 60)
    new_minute = int(rounded_minutes % 60)
    return dt.replace(hour=new_hour, minute=new_minute, second=0, microsecond=0)


def _select_latest_per_slot(
    interval_ids: list[str], interval_minutes: int
) -> list[str]:
    """Given interval IDs (newest first from zrevrange), pick one per time slot."""
    seen: set[str] = set()
    selected: list[str] = []
    for iid in interval_ids:
        try:
            dt = datetime.fromisoformat(iid.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        ist = dt.astimezone(IST)
        slot_key = _round_to_interval(ist, interval_minutes).strftime("%H:%M")
        if slot_key not in seen:
            seen.add(slot_key)
            selected.append(iid)
    return selected


def _lookup_pcr_score(pcr: float | None) -> dict[str, Any]:
    if pcr is None:
        return {"score": 0.0, "trend": "NEUTRAL", "label": "-"}
    try:
        v = float(pcr)
    except (ValueError, TypeError):
        return {"score": 0.0, "trend": "NEUTRAL", "label": "-"}

    for row in PCR_TABLE:
        if row["min"] <= v < row["max"]:
            distance = v - row["less"] if row["trend"] == "BULLISH" else row["less"] - v
            score = min(
                100.0,
                max(
                    0.0,
                    row["add"] + (distance / row["divide"]) * row["into"],
                ),
            )
            return {
                "score": score,
                "trend": row["trend"],
                "label": row["label"],
            }
    return {"score": 0.0, "trend": "NEUTRAL", "label": "-"}


def _compute_pcr(latest: dict | None) -> dict[str, Any]:
    types = [
        {"name": "COI PCR", "key": "coi_pcr_window", "weight": 0.50},
        {"name": "Strength PCR", "key": "strength_pcr", "weight": 0.30},
        {"name": "ATM PCR", "key": "atm_pcr", "weight": 0.20},
    ]
    coi_pcr = latest.get("coi_pcr_window") if latest else None
    main_trend = "NEUTRAL"
    if coi_pcr is not None:
        try:
            coi_val = float(coi_pcr)
            if coi_val > 1.0:
                main_trend = "BULLISH"
            elif coi_val < 1.0:
                main_trend = "BEARISH"
        except (ValueError, TypeError):
            pass

    weighted_score = 0.0
    for t in types:
        pcr_val = latest.get(t["key"]) if latest else None
        lookup = _lookup_pcr_score(pcr_val)
        weighted_score += lookup["score"] * t["weight"]

    return {
        "trend": main_trend,
        "score": weighted_score / 100.0,
        "coi_pcr": coi_pcr,
    }


def _compute_a2d(advance: int, decline: int) -> dict[str, Any]:
    total = advance + decline
    if total == 0:
        return {"trend": "NEUTRAL", "score": 0.0, "advance": 0, "decline": 0}
    if advance >= decline:
        return {
            "trend": "BULLISH",
            "score": advance / total,
            "advance": advance,
            "decline": decline,
        }
    return {
        "trend": "BEARISH",
        "score": decline / total,
        "advance": advance,
        "decline": decline,
    }


def _compute_indices(indices_dict: dict) -> dict[str, Any]:
    pos = indices_dict.get("advance", 0) or 0
    neg = indices_dict.get("decline", 0) or 0
    unc = indices_dict.get("unchanged", 0) or 0
    total = indices_dict.get("total", 0) or (pos + neg + unc)
    if total == 0:
        return {"trend": "NEUTRAL", "score": 0.0}
    if pos >= neg:
        return {"trend": "BULLISH", "score": pos / total}
    return {"trend": "BEARISH", "score": neg / total}


def _pvwap_score(pct: float) -> float:
    abs_pct = abs(pct)
    if abs_pct < 0.05:
        return 0.0
    if abs_pct < 0.10:
        return 0.25
    if abs_pct < 0.15:
        return 0.50
    if abs_pct < 0.20:
        return 0.75
    return 1.0


def _compute_price_vwap(strikes: list[dict]) -> dict[str, Any]:
    if not strikes:
        return {"trend": "NEUTRAL", "score": 0.0}
    call_sum = 0.0
    call_n = 0
    put_sum = 0.0
    put_n = 0
    for s in strikes:
        call_ltp = s.get("call_live_ltp") or s.get("call_ltp")
        put_ltp = s.get("put_live_ltp") or s.get("put_ltp")
        call_vwap = s.get("call_live_avg_price") or s.get("call_avg_price")
        put_vwap = s.get("put_live_avg_price") or s.get("put_avg_price")
        if call_ltp is not None and call_vwap is not None:
            try:
                cv = float(call_vwap)
                if cv > 0:
                    p = (float(call_ltp) - cv) / cv
                    call_sum += p
                    call_n += 1
            except (ValueError, TypeError):
                pass
        if put_ltp is not None and put_vwap is not None:
            try:
                pv = float(put_vwap)
                if pv > 0:
                    p = (float(put_ltp) - pv) / pv
                    put_sum += p
                    put_n += 1
            except (ValueError, TypeError):
                pass

    call_avg = call_sum / call_n if call_n > 0 else 0.0
    put_avg = put_sum / put_n if put_n > 0 else 0.0
    is_bullish = call_avg > put_avg
    trend = "BULLISH" if is_bullish else "BEARISH"
    bull_w = 0.70 if is_bullish else 0.30
    bear_w = 0.30 if is_bullish else 0.70

    call_score = _pvwap_score(call_avg)
    put_score = _pvwap_score(put_avg)
    side_score = (
        (call_score * bull_w + put_score * bear_w)
        if is_bullish
        else (put_score * bear_w + call_score * bull_w)
    )
    return {"trend": trend, "score": side_score}


def _coi_gap_score(gap: float) -> float:
    abs_gap = abs(gap)
    if abs_gap <= 0.10:
        return 0.50
    if abs_gap <= 0.20:
        return 0.60
    if abs_gap <= 0.35:
        return 0.70
    if abs_gap <= 0.50:
        return 0.85
    return 0.95


def _compute_coi_ratio(strikes: list[dict]) -> dict[str, Any]:
    if not strikes:
        return {"trend": "NEUTRAL", "score": 0.0}
    call_coi = 0.0
    put_coi = 0.0
    call_oi = 0.0
    put_oi = 0.0
    for s in strikes:
        try:
            call_coi += float(s.get("call_oi_change") or 0)
            put_coi += float(s.get("put_oi_change") or 0)
            call_oi += float(s.get("call_oi") or 0)
            put_oi += float(s.get("put_oi") or 0)
        except (ValueError, TypeError):
            pass

    total_coi = call_coi + put_coi
    total_oi = call_oi + put_oi
    coi_put_pct = (put_coi / total_coi) if total_coi > 0 else 0.5
    coi_call_pct = (call_coi / total_coi) if total_coi > 0 else 0.5
    oi_put_pct = (put_oi / total_oi) if total_oi > 0 else 0.5
    oi_call_pct = (call_oi / total_oi) if total_oi > 0 else 0.5

    coi_gap = coi_put_pct - coi_call_pct
    oi_gap = oi_put_pct - oi_call_pct

    coi_score_val = _coi_gap_score(coi_gap)
    oi_score_val = _coi_gap_score(oi_gap)
    final_score = coi_score_val * 0.70 + oi_score_val * 0.30

    coi_bullish = put_coi > call_coi
    oi_bullish = put_oi > call_oi
    conflict = coi_bullish != oi_bullish
    if conflict:
        final_score *= 0.85

    trend = "BULLISH" if coi_bullish else "BEARISH"
    return {"trend": trend, "score": final_score}


def _compute_overall(components: list[dict]) -> dict[str, Any]:
    bull_comps = [c for c in components if c.get("trend") == "BULLISH"]
    bear_comps = [c for c in components if c.get("trend") == "BEARISH"]
    bull_weight = sum(c.get("weight", 0) for c in bull_comps)
    bear_weight = sum(c.get("weight", 0) for c in bear_comps)
    overall = sum(c.get("score", 0) * c.get("weight", 0) for c in components)
    if bull_weight > bear_weight:
        trend = "BULLISH"
    elif bear_weight > bull_weight:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"
    return {
        "overall": overall,
        "trend": trend,
        "bull_weight": bull_weight,
        "bear_weight": bear_weight,
        "bull_count": len(bull_comps),
        "bear_count": len(bear_comps),
    }


def _compute_strategy_and_notes(
    latest: dict | None, strikes: list[dict], overall_trend: str
) -> dict[str, Any]:
    coi_pcr = None
    atm_pcr = None
    str_pcr = None

    if latest:
        try:
            if latest.get("coi_pcr_window") is not None:
                coi_pcr = float(latest["coi_pcr_window"])
            if latest.get("atm_pcr") is not None:
                atm_pcr = float(latest["atm_pcr"])
            if latest.get("strength_pcr") is not None:
                str_pcr = float(latest["strength_pcr"])
        except (ValueError, TypeError):
            pass

    trading_strategy = "NO TRADE ZONE"
    trading_strategy_bias = "NEUTRAL"
    data_trend = "NEUTRAL"

    if coi_pcr is not None and atm_pcr is not None and str_pcr is not None:
        all_above_buy = coi_pcr > 1.25 and atm_pcr > 1.25 and str_pcr > 1.25
        all_below_sell = coi_pcr < 0.70 and atm_pcr < 0.70 and str_pcr < 0.70
        negative_trend = coi_pcr < 0.75 and atm_pcr < 1.00 and str_pcr < 0.75

        if all_above_buy:
            trading_strategy = "BUY ON DIPS"
            trading_strategy_bias = "BULLISH"
        elif all_below_sell:
            trading_strategy = "SELL ON RISE"
            trading_strategy_bias = "BEARISH"

        if all_above_buy:
            data_trend = "POSITIVE"
        elif negative_trend:
            data_trend = "NEGATIVE"

    call_oi_sum = sum(float(s.get("call_oi") or 0) for s in strikes)
    put_oi_sum = sum(float(s.get("put_oi") or 0) for s in strikes)

    reversal_note = ""
    if trading_strategy == "BUY ON DIPS" and call_oi_sum > put_oi_sum:
        reversal_note = "MAY REVERSE FROM RESISTANCE"
    elif trading_strategy == "SELL ON RISE" and put_oi_sum > call_oi_sum:
        reversal_note = "MAY REVERSE FROM SUPPORT"

    signal_note_map = {
        "BULLISH-BEARISH": "BULLS MAY GET TRAP",
        "BULLISH-BULLISH": "STRONG BULLISH",
        "BEARISH-BULLISH": "BEARS MAY GET TRAP",
        "BEARISH-BEARISH": "STRONG BEARISH",
        "NEUTRAL-BULLISH": "UPSIDE BIASED - WAIT FOR OPTION CHAIN CONFIRMATION",
        "NEUTRAL-BEARISH": "DOWNSIDE BIASED - WAIT FOR OPTION CHAIN CONFIRMATION",
        "NEUTRAL-NEUTRAL": "WAIT FOR CONFIRMATION",
    }
    note_key = f"{trading_strategy_bias}-{overall_trend}"
    signal_note = signal_note_map.get(note_key, "")

    final_note = reversal_note if reversal_note else signal_note
    return {
        "trading_strategy": trading_strategy,
        "trading_strategy_bias": trading_strategy_bias,
        "data_trend": data_trend,
        "reversal_note": reversal_note,
        "signal_note": signal_note,
        "final_note": final_note,
    }


class HistoricalScoringService:
    """Service for computing live and 5-minute historical scoring analytics."""

    @staticmethod
    def _get_time_slots(trade_date: date) -> list[datetime]:
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

    @classmethod
    async def get_historical_scoring_data(
        cls, symbol: str | None = None
    ) -> dict[str, Any]:
        """Fetch and calculate historical scoring snapshot rows and top KPIs."""
        instruments = InstrumentCatalogService.get_active_instruments()
        if not instruments:
            return {
                "instrument": None,
                "market_date": datetime.now(IST).date().isoformat(),
                "kpis": None,
                "time_slots": [],
                "rows": [],
            }

        instrument = instruments[0]
        if symbol:
            for inst in instruments:
                if inst.symbol.upper() == symbol.upper():
                    instrument = inst
                    break

        trade_date = datetime.now(IST).date()
        time_slots = cls._get_time_slots(trade_date)
        time_slot_keys = [slot.strftime("%H:%M") for slot in time_slots]

        # 1. Fetch Option Chain timeline snapshots (deduplicated by 5-minute slot)
        timeline: list[dict[str, Any]] = []
        try:
            interval_ids = (
                await RedisOptionChainSnapshotStore.get_timeline_interval_ids(
                    instrument_symbol=instrument.symbol,
                    trade_date=trade_date.isoformat(),
                )
            )
            selected_ids = _select_latest_per_slot(
                interval_ids, COI_LIVE_INTERVAL_MINUTES
            )
            timeline = (
                await RedisOptionChainSnapshotStore.get_snapshots_by_interval_ids(
                    instrument_symbol=instrument.symbol,
                    trade_date=trade_date.isoformat(),
                    interval_ids=selected_ids,
                )
            )
        except Exception as e:
            logger.warning(f"Failed to get option chain timeline from Redis: {e}")

        # 2. Fetch Latest Index & Script Breadth Summary
        breadth_data: dict[str, Any] = {}
        try:
            index_snapshot = await RuntimeIndexSnapshotService.get_latest_snapshot()
            index_breadth = await RuntimeIndexSnapshotService.get_breadth_by_category(
                snapshot=index_snapshot
            )
            script_breadth = (
                await RuntimeScriptSnapshotService.get_breadth_by_category()
            )
            breadth_data = {
                "broad_market": {
                    "indices": index_breadth.get(
                        "BROAD_MARKET",
                        {
                            "advance": 0,
                            "decline": 0,
                            "unchanged": 0,
                            "total": 0,
                        },
                    ),
                    "scripts": script_breadth.get(
                        "BROAD_MARKET",
                        {
                            "advance": 0,
                            "decline": 0,
                            "unchanged": 0,
                            "total": 0,
                        },
                    ),
                },
                "sectoral": {
                    "indices": index_breadth.get(
                        "SECTORAL",
                        {
                            "advance": 0,
                            "decline": 0,
                            "unchanged": 0,
                            "total": 0,
                        },
                    ),
                    "scripts": script_breadth.get(
                        "SECTORAL",
                        {
                            "advance": 0,
                            "decline": 0,
                            "unchanged": 0,
                            "total": 0,
                        },
                    ),
                },
            }
        except Exception as e:
            logger.warning(f"Failed to get breadth data: {e}")

        # Compute static/current breadth components (used across snapshots)
        bm_scr = breadth_data.get("broad_market", {}).get("scripts", {})
        sl_scr = breadth_data.get("sectoral", {}).get("scripts", {})
        total_scr_adv = (bm_scr.get("advance") or 0) + (sl_scr.get("advance") or 0)
        total_scr_dec = (bm_scr.get("decline") or 0) + (sl_scr.get("decline") or 0)
        a2d_broad_comp = _compute_a2d(total_scr_adv, total_scr_dec)

        bm_idx = breadth_data.get("broad_market", {}).get("indices", {})
        sl_idx = breadth_data.get("sectoral", {}).get("indices", {})
        indices_comp = _compute_indices(bm_idx)
        sectors_comp = _compute_indices(sl_idx)

        # 3. Map snapshots by 5-minute time slot (e.g. "09:15")
        snapshot_by_slot: dict[str, dict[str, Any]] = {}
        for snap in timeline:
            captured_at = snap.get("latest", {}).get("captured_at")
            if not captured_at:
                continue
            try:
                captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
                captured_ist = captured_dt.astimezone(IST)
                rounded_ist = _round_to_interval(
                    captured_ist, COI_LIVE_INTERVAL_MINUTES
                )
                slot_key = rounded_ist.strftime("%H:%M")
                if slot_key not in snapshot_by_slot:
                    snapshot_by_slot[slot_key] = snap
            except Exception:
                continue

        # 4. Generate table rows for each time slot (from 09:15 to 15:30)
        table_rows: list[dict[str, Any]] = []
        latest_snapshot_for_kpis: dict[str, Any] | None = (
            timeline[0] if timeline else None
        )

        for slot_key in time_slot_keys:
            snap = snapshot_by_slot.get(slot_key)
            if not snap:
                continue

            latest_data = snap.get("latest", {})
            strikes = snap.get("strikes", [])

            # Compute components
            pcr_comp = _compute_pcr(latest_data)
            pvwap_comp = _compute_price_vwap(strikes)
            coi_ratio_comp = _compute_coi_ratio(strikes)

            components = [
                {"name": "PCR", "weight": 0.15, **pcr_comp},
                {"name": "A2D Broader", "weight": 0.15, **a2d_broad_comp},
                {"name": "Indices", "weight": 0.20, **indices_comp},
                {"name": "Sectors", "weight": 0.20, **sectors_comp},
                {"name": "Price vs VWAP", "weight": 0.05, **pvwap_comp},
                {"name": "COI Ratio", "weight": 0.15, **coi_ratio_comp},
            ]

            overall_res = _compute_overall(components)
            strategy_res = _compute_strategy_and_notes(
                latest_data, strikes, overall_res["trend"]
            )

            table_rows.append(
                {
                    "time": slot_key,
                    "trend": overall_res["trend"],
                    "note": strategy_res["final_note"],
                    "overall_scoring": overall_res["overall"],
                    "pcr": {
                        "score": pcr_comp["score"],
                        "trend": pcr_comp["trend"],
                        "value": pcr_comp.get("coi_pcr"),
                    },
                    "a2d_broad": {
                        "score": a2d_broad_comp["score"],
                        "trend": a2d_broad_comp["trend"],
                    },
                    "indices": {
                        "score": indices_comp["score"],
                        "trend": indices_comp["trend"],
                    },
                    "sectors": {
                        "score": sectors_comp["score"],
                        "trend": sectors_comp["trend"],
                    },
                    "price_vs_vwap": {
                        "score": pvwap_comp["score"],
                        "trend": pvwap_comp["trend"],
                    },
                    "coi_ratio": {
                        "score": coi_ratio_comp["score"],
                        "trend": coi_ratio_comp["trend"],
                    },
                }
            )

        # 5. Compute Top KPIs (Gauge, Trends, Strategy, Money Flow, Supports, Resistance)
        kpis_data = None
        if latest_snapshot_for_kpis:
            kpi_latest = latest_snapshot_for_kpis.get("latest", {})
            kpi_strikes = latest_snapshot_for_kpis.get("strikes", [])

            # Components for latest overall score
            kpi_pcr = _compute_pcr(kpi_latest)
            kpi_pvwap = _compute_price_vwap(kpi_strikes)
            kpi_coi_ratio = _compute_coi_ratio(kpi_strikes)

            kpi_components = [
                {"name": "PCR", "weight": 0.15, **kpi_pcr},
                {"name": "A2D Broader", "weight": 0.15, **a2d_broad_comp},
                {"name": "Indices", "weight": 0.20, **indices_comp},
                {"name": "Sectors", "weight": 0.20, **sectors_comp},
                {"name": "Price vs VWAP", "weight": 0.05, **kpi_pvwap},
                {"name": "COI Ratio", "weight": 0.15, **kpi_coi_ratio},
            ]
            kpi_overall = _compute_overall(kpi_components)
            kpi_strategy = _compute_strategy_and_notes(
                kpi_latest, kpi_strikes, kpi_overall["trend"]
            )

            # Money Flow Calculations:
            # - Average VWAP across strikes (matching dashboard footer avgOf)
            call_vwaps = [
                float(s.get("call_live_avg_price") or s.get("call_avg_price"))
                for s in kpi_strikes
                if (
                    s.get("call_live_avg_price") is not None
                    or s.get("call_avg_price") is not None
                )
            ]
            put_vwaps = [
                float(s.get("put_live_avg_price") or s.get("put_avg_price"))
                for s in kpi_strikes
                if (
                    s.get("put_live_avg_price") is not None
                    or s.get("put_avg_price") is not None
                )
            ]

            avg_call_vwap = sum(call_vwaps) / len(call_vwaps) if call_vwaps else 0.0
            avg_put_vwap = sum(put_vwaps) / len(put_vwaps) if put_vwaps else 0.0

            total_call_oi = sum(float(s.get("call_oi") or 0) for s in kpi_strikes)
            total_put_oi = sum(float(s.get("put_oi") or 0) for s in kpi_strikes)
            total_call_coi = sum(
                float(s.get("call_oi_change") or 0) for s in kpi_strikes
            )
            total_put_coi = sum(float(s.get("put_oi_change") or 0) for s in kpi_strikes)

            money_flow_overall_call = total_call_oi * avg_call_vwap
            money_flow_overall_put = total_put_oi * avg_put_vwap
            money_flow_intraday_call = total_call_coi * avg_call_vwap
            money_flow_intraday_put = total_put_coi * avg_put_vwap

            # Support & Resistance rankings:
            # - Resistance = Call side (Top 5 strikes by Call OI descending)
            sorted_call_strikes = sorted(
                kpi_strikes,
                key=lambda s: float(s.get("call_oi") or 0),
                reverse=True,
            )[:5]

            resistances = []
            for idx, s in enumerate(sorted_call_strikes):
                strike_price = s.get("strike_price")
                call_oi = float(s.get("call_oi") or 0)
                call_coi = float(s.get("call_oi_change") or 0)
                call_vwap = float(
                    s.get("call_live_avg_price") or s.get("call_avg_price") or 0.0
                )
                resistances.append(
                    {
                        "level": f"R{idx + 1} - {int(strike_price) if strike_price is not None else '-'}",
                        "strike_price": strike_price,
                        "overall_money_flow": call_oi * call_vwap,
                        "today_money_flow": call_coi * call_vwap,
                    }
                )

            # - Support = Put side (Top 5 strikes by Put OI descending)
            sorted_put_strikes = sorted(
                kpi_strikes,
                key=lambda s: float(s.get("put_oi") or 0),
                reverse=True,
            )[:5]

            supports = []
            for idx, s in enumerate(sorted_put_strikes):
                strike_price = s.get("strike_price")
                put_oi = float(s.get("put_oi") or 0)
                put_coi = float(s.get("put_oi_change") or 0)
                put_vwap = float(
                    s.get("put_live_avg_price") or s.get("put_avg_price") or 0.0
                )
                supports.append(
                    {
                        "level": f"S{idx + 1} - {int(strike_price) if strike_price is not None else '-'}",
                        "strike_price": strike_price,
                        "overall_money_flow": put_oi * put_vwap,
                        "today_money_flow": put_coi * put_vwap,
                    }
                )

            kpis_data = {
                "overall_score": kpi_overall["overall"],
                "market_trend": kpi_overall["trend"],
                "data_trend": kpi_strategy["data_trend"],
                "trading_strategy": kpi_strategy["trading_strategy"],
                "money_flow": {
                    "overall": {
                        "call": money_flow_overall_call,
                        "put": money_flow_overall_put,
                    },
                    "intraday": {
                        "call": money_flow_intraday_call,
                        "put": money_flow_intraday_put,
                    },
                },
                "supports": supports,
                "resistances": resistances,
            }

        return {
            "instrument": {
                "id": str(getattr(instrument, "id", instrument.symbol)),
                "symbol": instrument.symbol,
                "name": getattr(instrument, "name", None) or instrument.symbol,
            },
            "market_date": trade_date.isoformat(),
            "kpis": kpis_data,
            "time_slots": time_slot_keys,
            "rows": table_rows,
        }
