import argparse
import asyncio
import sys
from pathlib import Path

BASE_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, BASE_DIR)
from libs.utils.db.postgres.operations.src import (  # noqa: E402
    OptionSnapshotOperations,
)


async def run_backfill(batch_size: int, max_snapshots: int | None):
    result = await OptionSnapshotOperations.backfill_missing_summaries(
        batch_size=batch_size,
        max_snapshots=max_snapshots,
    )
    print("Backfill completed:", result)


def main():
    parser = argparse.ArgumentParser(
        description="Backfill option_chain_interval_summaries and option_chain_strike_summaries from historical snapshots."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Number of snapshots to process per batch (default: 200).",
    )
    parser.add_argument(
        "--max-snapshots",
        type=int,
        default=None,
        help="Optional cap on total snapshots to process.",
    )
    args = parser.parse_args()
    asyncio.run(run_backfill(args.batch_size, args.max_snapshots))


if __name__ == "__main__":
    main()
