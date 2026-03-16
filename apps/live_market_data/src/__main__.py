import asyncio
import sys
from pathlib import Path

BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)
sys.path.append(BASE_DIR)

from apps.live_market_data.src import run_live_market_data_app  # noqa: E402

if __name__ == "__main__":
    asyncio.run(run_live_market_data_app())
