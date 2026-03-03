import asyncio
import sys
from pathlib import Path

BASE_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, BASE_DIR)

from libs.utils.db.postgres.models.src.instrument import (  # noqa: E402
    Instrument,
)
from libs.utils.db.postgres.src.connection import (  # noqa: E402
    postgres_connection,
)

SEED_INSTRUMENTS = [
    {
        "symbol": "NIFTY",
        "fyers_symbol": "NSE:NIFTY50-INDEX",
        "exchange": "NSE",
        "instrument_type": "INDEX",
        "is_active": True,
    },
]


async def seed():
    async with postgres_connection.get_session() as session:
        for data in SEED_INSTRUMENTS:
            from sqlalchemy import select

            stmt = select(Instrument).where(Instrument.symbol == data["symbol"])
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                print(f"Instrument '{data['symbol']}' already exists, skipping.")
            else:
                instrument = Instrument(**data)
                session.add(instrument)
                await session.flush()
                print(f"Seeded instrument: {data['symbol']}")

        await session.commit()
    print("Seed completed.")


if __name__ == "__main__":
    asyncio.run(seed())
