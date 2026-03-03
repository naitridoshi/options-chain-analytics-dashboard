from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.db.postgres.models.src.expiry import Expiry
from libs.utils.db.postgres.models.src.option_chain_snapshot import (
    OptionChainSnapshot,
)
from libs.utils.db.postgres.models.src.option_contract import OptionContract
from libs.utils.db.postgres.operations.src.base import BaseOperations
from libs.utils.db.postgres.src.connection import postgres_connection
from libs.utils.db.postgres.src.repository import (
    get_expiries_repository,
    get_option_chain_snapshots_repository,
    get_option_chain_strikes_repository,
    get_option_contracts_repository,
)

log = CustomLogger("OptionSnapshotOperations")
logger, listener = log.get_logger()
listener.start()


class OptionSnapshotOperations(BaseOperations[OptionChainSnapshot]):
    """
    DB orchestration for one snapshot transaction.
    """

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
                    "Auto-created option contracts",
                    extra={"count": len(contracts_to_create)},
                )

            snapshot = OptionChainSnapshot(
                instrument_id=instrument_id,
                expiry_id=expiry.id,
                captured_at=captured_at,
                spot_price=spot_price,
            )
            await snapshot_repo.add(snapshot, commit=False, refresh=False)

            strike_values = []
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

            await strike_repo.bulk_insert(strike_values, commit=False)

            logger.info(
                "Snapshot created",
                extra={
                    "instrument_id": str(instrument_id),
                    "expiry_date": str(expiry_date),
                    "snapshot_id": str(snapshot.id),
                    "strikes": len(strike_values),
                },
            )

            return {
                "snapshot_id": snapshot.id,
                "strikes_inserted": len(strike_values),
                "expiry_id": expiry.id,
            }
