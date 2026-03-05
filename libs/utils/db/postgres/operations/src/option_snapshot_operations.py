from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.db.postgres.models.src.expiry import Expiry
from libs.utils.db.postgres.models.src.option_chain_interval_summary import (
    OptionChainIntervalSummary,
)
from libs.utils.db.postgres.models.src.option_chain_snapshot import (
    OptionChainSnapshot,
)
from libs.utils.db.postgres.models.src.option_chain_strike_summary import (
    OptionChainStrikeSummary,
)
from libs.utils.db.postgres.models.src.option_contract import OptionContract
from libs.utils.db.postgres.operations.src.base import BaseOperations
from libs.utils.db.postgres.src.connection import postgres_connection
from libs.utils.db.postgres.src.repository import (
    get_expiries_repository,
    get_option_chain_interval_summaries_repository,
    get_option_chain_snapshots_repository,
    get_option_chain_strike_summaries_repository,
    get_option_chain_strikes_repository,
    get_option_contracts_repository,
)

log = CustomLogger("OptionSnapshotOperations")
logger, listener = log.get_logger()
listener.start()

IST = ZoneInfo("Asia/Kolkata")


class OptionSnapshotOperations(BaseOperations[OptionChainSnapshot]):
    """
    DB orchestration for one snapshot transaction.
    """

    @staticmethod
    def _build_summary_payloads(summary_rows: list[dict]) -> tuple[dict, list[dict]]:
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
        call_oi_change_sum = 0
        put_oi_change_sum = 0
        call_oi_sum = 0
        put_oi_sum = 0
        call_volume_sum = 0
        put_volume_sum = 0

        for row in summary_rows:
            option_type = str(row.get("option_type", "")).upper()
            row_oi_change = safe_int(row.get("oi_change"))
            row_oi = safe_int(row.get("open_interest"))
            row_volume = safe_int(row.get("volume"))
            row_ltp = safe_decimal(row.get("ltp"))
            strike_price = Decimal(str(row["strike_price"]))

            strike_entry = strike_agg.setdefault(
                strike_price,
                {
                    "strike_price": strike_price,
                    "call_option_contract_id": None,
                    "put_option_contract_id": None,
                    "call_oi_change": 0,
                    "put_oi_change": 0,
                    "call_oi": 0,
                    "put_oi": 0,
                    "call_volume": 0,
                    "put_volume": 0,
                    "call_ltp": None,
                    "put_ltp": None,
                },
            )

            if option_type == "CE":
                strike_entry["call_option_contract_id"] = row.get("option_contract_id")
                strike_entry["call_oi_change"] = row_oi_change
                strike_entry["call_oi"] = row_oi
                strike_entry["call_volume"] = row_volume
                strike_entry["call_ltp"] = row_ltp
                call_oi_change_sum += row_oi_change
                call_oi_sum += row_oi
                call_volume_sum += row_volume
            elif option_type == "PE":
                strike_entry["put_option_contract_id"] = row.get("option_contract_id")
                strike_entry["put_oi_change"] = row_oi_change
                strike_entry["put_oi"] = row_oi
                strike_entry["put_volume"] = row_volume
                strike_entry["put_ltp"] = row_ltp
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
            (Decimal(call_oi_change_sum) * Decimal("100"))
            / Decimal(total_oi_change_sum)
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
                    "call_oi_change": item["call_oi_change"],
                    "put_oi_change": item["put_oi_change"],
                    "net_oi_change": item["put_oi_change"] - item["call_oi_change"],
                    "call_oi": item["call_oi"],
                    "put_oi": item["put_oi"],
                    "net_oi": item["put_oi"] - item["call_oi"],
                    "call_volume": item["call_volume"],
                    "put_volume": item["put_volume"],
                    "call_ltp": item["call_ltp"],
                    "put_ltp": item["put_ltp"],
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

    @classmethod
    async def create_snapshot_transactional(
        cls,
        *,
        instrument_id: UUID,
        expiry_date: date,
        is_weekly: bool,
        captured_at: datetime,
        spot_price: Decimal,
        strike_rows: list[dict],
    ) -> dict:
        async with postgres_connection.get_session() as session:
            expiry_repo = get_expiries_repository(session)
            contract_repo = get_option_contracts_repository(session)
            snapshot_repo = get_option_chain_snapshots_repository(session)
            strike_repo = get_option_chain_strikes_repository(session)
            interval_summary_repo = get_option_chain_interval_summaries_repository(
                session
            )
            strike_summary_repo = get_option_chain_strike_summaries_repository(session)

            expiry = await expiry_repo.get(
                [
                    Expiry.instrument_id == instrument_id,
                    Expiry.expiry_date == expiry_date,
                ]
            )
            if not expiry:
                expiry = Expiry(
                    instrument_id=instrument_id,
                    expiry_date=expiry_date,
                    is_weekly=is_weekly,
                )
                await expiry_repo.add(expiry, commit=False, refresh=False)

            existing_contracts = await contract_repo.list_ordered(
                where=OptionContract.expiry_id == expiry.id,
                limit=10000,
            )
            contract_map = {
                (str(contract.strike_price), contract.option_type): contract
                for contract in existing_contracts
            }

            contracts_to_create = []
            for row in strike_rows:
                key = (str(row["strike_price"]), row["option_type"])
                if key in contract_map:
                    continue
                contract = OptionContract(
                    instrument_id=instrument_id,
                    expiry_id=expiry.id,
                    strike_price=row["strike_price"],
                    option_type=row["option_type"],
                    trading_symbol=row["trading_symbol"],
                    lot_size=row.get("lot_size"),
                )
                contracts_to_create.append(contract)
                contract_map[key] = contract

            if contracts_to_create:
                await contract_repo.add_many(
                    contracts_to_create, commit=False, refresh=False
                )
                logger.info(
                    "Auto-created option contracts - "
                    f"instrument_id: {instrument_id} - "
                    f"count: {len(contracts_to_create)}",
                )

            snapshot = OptionChainSnapshot(
                instrument_id=instrument_id,
                expiry_id=expiry.id,
                captured_at=captured_at,
                spot_price=spot_price,
            )
            await snapshot_repo.add(snapshot, commit=False, refresh=False)

            strike_values = []
            summary_rows = []

            for row in strike_rows:
                key = (str(row["strike_price"]), row["option_type"])
                contract = contract_map[key]

                strike_values.append(
                    {
                        "snapshot_id": snapshot.id,
                        "option_contract_id": contract.id,
                        "ltp": row.get("ltp"),
                        "volume": row.get("volume"),
                        "open_interest": row.get("open_interest"),
                        "oi_change": row.get("oi_change"),
                        "implied_volatility": row.get("implied_volatility"),
                        "bid_price": row.get("bid_price"),
                        "bid_qty": row.get("bid_qty"),
                        "ask_price": row.get("ask_price"),
                        "ask_qty": row.get("ask_qty"),
                    }
                )
                summary_rows.append(
                    {
                        "option_contract_id": contract.id,
                        "option_type": row.get("option_type"),
                        "strike_price": row.get("strike_price"),
                        "oi_change": row.get("oi_change"),
                        "open_interest": row.get("open_interest"),
                        "volume": row.get("volume"),
                        "ltp": row.get("ltp"),
                    }
                )

            await strike_repo.bulk_insert(strike_values, commit=False)

            interval_summary_values, strike_summary_values = (
                cls._build_summary_payloads(summary_rows)
            )

            interval_summary = OptionChainIntervalSummary(
                snapshot_id=snapshot.id,
                instrument_id=instrument_id,
                expiry_id=expiry.id,
                captured_at=captured_at,
                spot_price=spot_price,
                call_oi_change_sum=interval_summary_values["call_oi_change_sum"],
                put_oi_change_sum=interval_summary_values["put_oi_change_sum"],
                net_oi_change_sum=interval_summary_values["net_oi_change_sum"],
                call_oi_sum=interval_summary_values["call_oi_sum"],
                put_oi_sum=interval_summary_values["put_oi_sum"],
                net_oi_sum=interval_summary_values["net_oi_sum"],
                call_volume_sum=interval_summary_values["call_volume_sum"],
                put_volume_sum=interval_summary_values["put_volume_sum"],
                pcr_oi=interval_summary_values["pcr_oi"],
                pcr_oi_change=interval_summary_values["pcr_oi_change"],
                call_oi_share_pct=interval_summary_values["call_oi_share_pct"],
                put_oi_share_pct=interval_summary_values["put_oi_share_pct"],
                call_oi_change_share_pct=interval_summary_values[
                    "call_oi_change_share_pct"
                ],
                put_oi_change_share_pct=interval_summary_values[
                    "put_oi_change_share_pct"
                ],
            )
            await interval_summary_repo.add(
                interval_summary,
                commit=False,
                refresh=False,
            )

            strike_summary_entities = []
            for item in strike_summary_values:
                strike_summary_entities.append(
                    OptionChainStrikeSummary(
                        snapshot_id=snapshot.id,
                        instrument_id=instrument_id,
                        expiry_id=expiry.id,
                        captured_at=captured_at,
                        strike_price=item["strike_price"],
                        call_option_contract_id=item["call_option_contract_id"],
                        put_option_contract_id=item["put_option_contract_id"],
                        call_oi_change=item["call_oi_change"],
                        put_oi_change=item["put_oi_change"],
                        net_oi_change=item["net_oi_change"],
                        call_oi=item["call_oi"],
                        put_oi=item["put_oi"],
                        net_oi=item["net_oi"],
                        call_volume=item["call_volume"],
                        put_volume=item["put_volume"],
                        call_ltp=item["call_ltp"],
                        put_ltp=item["put_ltp"],
                    )
                )

            if strike_summary_entities:
                await strike_summary_repo.add_many(
                    strike_summary_entities,
                    commit=False,
                    refresh=False,
                )

            logger.info(
                "Snapshot created - "
                f"instrument_id: {instrument_id} - "
                f"expiry_date: {expiry_date} - "
                f"snapshot_id: {snapshot.id} - "
                f"strikes_inserted: {len(strike_values)}",
            )

            return {
                "snapshot_id": snapshot.id,
                "strikes_inserted": len(strike_values),
                "expiry_id": expiry.id,
            }

    @classmethod
    async def backfill_missing_summaries(
        cls,
        *,
        batch_size: int = 200,
        max_snapshots: int | None = None,
    ) -> dict:
        batch_size = max(1, batch_size)
        async with postgres_connection.get_session() as session:
            snapshot_repo = get_option_chain_snapshots_repository(session)
            interval_summary_repo = get_option_chain_interval_summaries_repository(
                session
            )
            strike_summary_repo = get_option_chain_strike_summaries_repository(session)
            strike_repo = get_option_chain_strikes_repository(session)

            processed = 0
            backfilled_interval = 0
            backfilled_strike = 0
            skipped_no_strikes = 0
            offset = 0

            while True:
                if max_snapshots is not None and processed >= max_snapshots:
                    break
                current_limit = batch_size
                if max_snapshots is not None:
                    current_limit = min(current_limit, max_snapshots - processed)
                    if current_limit <= 0:
                        break

                snapshots = await snapshot_repo.list_ordered(
                    order_by=snapshot_repo.model.captured_at.asc(),
                    offset=offset,
                    limit=current_limit,
                )
                if not snapshots:
                    break

                snapshot_ids = [snapshot.id for snapshot in snapshots]
                existing_interval_snapshot_ids = (
                    await interval_summary_repo.get_existing_snapshot_ids(snapshot_ids)
                )
                existing_strike_snapshot_ids = (
                    await strike_summary_repo.get_existing_snapshot_ids(snapshot_ids)
                )

                for snapshot in snapshots:
                    needs_interval = snapshot.id not in existing_interval_snapshot_ids
                    needs_strike = snapshot.id not in existing_strike_snapshot_ids
                    if not needs_interval and not needs_strike:
                        continue

                    summary_rows = await strike_repo.get_summary_rows_for_snapshot(
                        snapshot.id
                    )
                    if not summary_rows:
                        skipped_no_strikes += 1
                        continue

                    interval_values, strike_values = cls._build_summary_payloads(
                        summary_rows
                    )

                    if needs_interval:
                        interval_summary = OptionChainIntervalSummary(
                            snapshot_id=snapshot.id,
                            instrument_id=snapshot.instrument_id,
                            expiry_id=snapshot.expiry_id,
                            captured_at=snapshot.captured_at,
                            spot_price=snapshot.spot_price,
                            call_oi_change_sum=interval_values["call_oi_change_sum"],
                            put_oi_change_sum=interval_values["put_oi_change_sum"],
                            net_oi_change_sum=interval_values["net_oi_change_sum"],
                            call_oi_sum=interval_values["call_oi_sum"],
                            put_oi_sum=interval_values["put_oi_sum"],
                            net_oi_sum=interval_values["net_oi_sum"],
                            call_volume_sum=interval_values["call_volume_sum"],
                            put_volume_sum=interval_values["put_volume_sum"],
                            pcr_oi=interval_values["pcr_oi"],
                            pcr_oi_change=interval_values["pcr_oi_change"],
                            call_oi_share_pct=interval_values["call_oi_share_pct"],
                            put_oi_share_pct=interval_values["put_oi_share_pct"],
                            call_oi_change_share_pct=interval_values[
                                "call_oi_change_share_pct"
                            ],
                            put_oi_change_share_pct=interval_values[
                                "put_oi_change_share_pct"
                            ],
                        )
                        await interval_summary_repo.add(
                            interval_summary,
                            commit=False,
                            refresh=False,
                        )
                        backfilled_interval += 1

                    if needs_strike and strike_values:
                        strike_entities = []
                        for item in strike_values:
                            strike_entities.append(
                                OptionChainStrikeSummary(
                                    snapshot_id=snapshot.id,
                                    instrument_id=snapshot.instrument_id,
                                    expiry_id=snapshot.expiry_id,
                                    captured_at=snapshot.captured_at,
                                    strike_price=item["strike_price"],
                                    call_option_contract_id=item[
                                        "call_option_contract_id"
                                    ],
                                    put_option_contract_id=item[
                                        "put_option_contract_id"
                                    ],
                                    call_oi_change=item["call_oi_change"],
                                    put_oi_change=item["put_oi_change"],
                                    net_oi_change=item["net_oi_change"],
                                    call_oi=item["call_oi"],
                                    put_oi=item["put_oi"],
                                    net_oi=item["net_oi"],
                                    call_volume=item["call_volume"],
                                    put_volume=item["put_volume"],
                                    call_ltp=item["call_ltp"],
                                    put_ltp=item["put_ltp"],
                                )
                            )
                        await strike_summary_repo.add_many(
                            strike_entities,
                            commit=False,
                            refresh=False,
                        )
                        backfilled_strike += len(strike_entities)

                processed += len(snapshots)
                offset += len(snapshots)

            logger.info(
                "Snapshot summary backfill complete - "
                f"processed_snapshots: {processed} - "
                f"interval_rows_inserted: {backfilled_interval} - "
                f"strike_rows_inserted: {backfilled_strike} - "
                f"skipped_no_strikes: {skipped_no_strikes}"
            )
            return {
                "processed_snapshots": processed,
                "interval_rows_inserted": backfilled_interval,
                "strike_rows_inserted": backfilled_strike,
                "skipped_no_strikes": skipped_no_strikes,
            }

    @classmethod
    async def get_latest_captured_at_for_today_ist(cls) -> datetime | None:
        now_ist = datetime.now(IST)
        start_of_day_ist = datetime.combine(
            now_ist.date(),
            time.min,
            tzinfo=IST,
        )
        start_of_next_day_ist = start_of_day_ist + timedelta(days=1)

        start_utc = start_of_day_ist.astimezone(timezone.utc)
        end_utc = start_of_next_day_ist.astimezone(timezone.utc)

        async with postgres_connection.get_session() as session:
            snapshot_repo = get_option_chain_snapshots_repository(session)
            snapshots = await snapshot_repo.list_ordered(
                where=[
                    snapshot_repo.model.captured_at >= start_utc,
                    snapshot_repo.model.captured_at < end_utc,
                ],
                order_by=snapshot_repo.model.captured_at.desc(),
                limit=1,
            )
            if not snapshots:
                return None
            return snapshots[0].captured_at
