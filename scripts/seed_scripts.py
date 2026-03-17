import asyncio
import sys
from pathlib import Path

BASE_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, BASE_DIR)

from libs.utils.common.constants.src.seeder import (  # noqa: E402
    SCRIPTS_FILE_PATH,
)
from libs.utils.db.postgres.operations.src import ScriptOperations  # noqa: E402


async def seed():
    seed_result = await ScriptOperations.seed_missing_scripts_from_file(
        SCRIPTS_FILE_PATH
    )
    for symbol in seed_result["inserted_symbols"]:
        print(f"[inserted] {symbol}")
    for symbol in seed_result["skipped_symbols"]:
        print(f"[skipped]  {symbol}")
    print(
        "Script seeding complete - "
        f"inserted={len(seed_result['inserted_symbols'])}, "
        f"skipped={len(seed_result['skipped_symbols'])}"
    )


if __name__ == "__main__":
    asyncio.run(seed())
