from __future__ import annotations

from decimal import Decimal


def build_summary_payloads(summary_rows: list[dict]) -> tuple[dict, list[dict]]:
    def safe_int(value) -> int:
        if value is None:
            return 0
        if isinstance(value, Decimal):
            return int(value)
        return int(value)

    def safe_decimal(value) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))

    strike_agg: dict[Decimal, dict] = {}
    seen_contract_keys: set[tuple[Decimal, str]] = set()
    call_oi_change_sum = 0
    put_oi_change_sum = 0
    call_oi_sum = 0
    put_oi_sum = 0
    call_volume_sum = 0
    put_volume_sum = 0

    for row in summary_rows:
        option_type = str(row.get("option_type", "")).upper()
        if option_type not in {"CE", "PE"}:
            continue
        row_oi_change = safe_int(row.get("oi_change"))
        row_oi = safe_int(row.get("open_interest"))
        row_volume = safe_int(row.get("volume"))
        row_ltp = safe_decimal(row.get("ltp"))
        row_ltp_change = safe_decimal(row.get("ltp_change"))
        strike_price = Decimal(str(row["strike_price"]))
        contract_key = (strike_price, option_type)
        if contract_key in seen_contract_keys:
            continue
        seen_contract_keys.add(contract_key)

        strike_entry = strike_agg.setdefault(
            strike_price,
            {
                "strike_price": strike_price,
                "call_option_contract_id": None,
                "put_option_contract_id": None,
                "call_trading_symbol": None,
                "put_trading_symbol": None,
                "call_oi_change": 0,
                "put_oi_change": 0,
                "call_oi": 0,
                "put_oi": 0,
                "call_volume": 0,
                "put_volume": 0,
                "call_ltp": None,
                "call_ltp_change": None,
                "put_ltp": None,
                "put_ltp_change": None,
            },
        )

        if option_type == "CE":
            strike_entry["call_option_contract_id"] = row.get("option_contract_id")
            strike_entry["call_trading_symbol"] = row.get("trading_symbol")
            strike_entry["call_oi_change"] = row_oi_change
            strike_entry["call_oi"] = row_oi
            strike_entry["call_volume"] = row_volume
            strike_entry["call_ltp"] = row_ltp
            strike_entry["call_ltp_change"] = row_ltp_change
            call_oi_change_sum += row_oi_change
            call_oi_sum += row_oi
            call_volume_sum += row_volume
        elif option_type == "PE":
            strike_entry["put_option_contract_id"] = row.get("option_contract_id")
            strike_entry["put_trading_symbol"] = row.get("trading_symbol")
            strike_entry["put_oi_change"] = row_oi_change
            strike_entry["put_oi"] = row_oi
            strike_entry["put_volume"] = row_volume
            strike_entry["put_ltp"] = row_ltp
            strike_entry["put_ltp_change"] = row_ltp_change
            put_oi_change_sum += row_oi_change
            put_oi_sum += row_oi
            put_volume_sum += row_volume

    net_oi_change_sum = put_oi_change_sum - call_oi_change_sum
    net_oi_sum = put_oi_sum - call_oi_sum
    pcr_oi = Decimal(put_oi_sum) / Decimal(call_oi_sum) if call_oi_sum > 0 else None
    pcr_oi_change = (
        Decimal(put_oi_change_sum) / Decimal(call_oi_change_sum)
        if call_oi_change_sum != 0
        else None
    )
    total_oi_sum = put_oi_sum + call_oi_sum
    total_oi_change_sum = put_oi_change_sum + call_oi_change_sum
    call_oi_share_pct = (
        (Decimal(call_oi_sum) * Decimal("100")) / Decimal(total_oi_sum)
        if total_oi_sum > 0
        else None
    )
    put_oi_share_pct = (
        (Decimal(put_oi_sum) * Decimal("100")) / Decimal(total_oi_sum)
        if total_oi_sum > 0
        else None
    )
    call_oi_change_share_pct = (
        (Decimal(call_oi_change_sum) * Decimal("100")) / Decimal(total_oi_change_sum)
        if total_oi_change_sum > 0
        else None
    )
    put_oi_change_share_pct = (
        (Decimal(put_oi_change_sum) * Decimal("100")) / Decimal(total_oi_change_sum)
        if total_oi_change_sum > 0
        else None
    )

    strike_summaries = []
    for item in strike_agg.values():
        strike_summaries.append(
            {
                "strike_price": item["strike_price"],
                "call_option_contract_id": item["call_option_contract_id"],
                "put_option_contract_id": item["put_option_contract_id"],
                "call_trading_symbol": item["call_trading_symbol"],
                "put_trading_symbol": item["put_trading_symbol"],
                "call_oi_change": item["call_oi_change"],
                "put_oi_change": item["put_oi_change"],
                "net_oi_change": item["put_oi_change"] - item["call_oi_change"],
                "call_oi": item["call_oi"],
                "put_oi": item["put_oi"],
                "net_oi": item["put_oi"] - item["call_oi"],
                "call_volume": item["call_volume"],
                "put_volume": item["put_volume"],
                "call_ltp": item["call_ltp"],
                "call_ltp_change": item["call_ltp_change"],
                "put_ltp": item["put_ltp"],
                "put_ltp_change": item["put_ltp_change"],
            }
        )

    interval_summary = {
        "call_oi_change_sum": call_oi_change_sum,
        "put_oi_change_sum": put_oi_change_sum,
        "net_oi_change_sum": net_oi_change_sum,
        "call_oi_sum": call_oi_sum,
        "put_oi_sum": put_oi_sum,
        "net_oi_sum": net_oi_sum,
        "call_volume_sum": call_volume_sum,
        "put_volume_sum": put_volume_sum,
        "pcr_oi": pcr_oi,
        "pcr_oi_change": pcr_oi_change,
        "call_oi_share_pct": call_oi_share_pct,
        "put_oi_share_pct": put_oi_share_pct,
        "call_oi_change_share_pct": call_oi_change_share_pct,
        "put_oi_change_share_pct": put_oi_change_share_pct,
    }
    return interval_summary, strike_summaries
