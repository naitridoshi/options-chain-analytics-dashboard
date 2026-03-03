import asyncio
import sys
from pathlib import Path

BASE_DIR = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, BASE_DIR)

from apps.scheduler.src import run_scheduler  # noqa: E402

if __name__ == "__main__":
    asyncio.run(run_scheduler())
