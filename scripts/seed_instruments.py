import asyncio
import sys
from pathlib import Path

from libs.utils.common.constants.src.seeder import INSTRUMENTS_FILE_PATH

BASE_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, BASE_DIR)

from libs.utils.db.postgres.operations.src import (  # noqa: E402
    InstrumentOperations,
)


async def seed():
    seed_result = await InstrumentOperations.seed_missing_instruments_from_file(
        INSTRUMENTS_FILE_PATH
    )
    for symbol in seed_result["inserted_symbols"]:
        print(f"Seeded instrument: {symbol}")
    for symbol in seed_result["skipped_symbols"]:
        print(f"Instrument '{symbol}' already exists, skipping.")
    print(
        "Seed completed."
        f" inserted={len(seed_result['inserted_symbols'])},"
        f" skipped={len(seed_result['skipped_symbols'])}"
    )


if __name__ == "__main__":
    asyncio.run(seed())
