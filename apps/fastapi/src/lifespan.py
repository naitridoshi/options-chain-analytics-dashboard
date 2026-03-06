from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.utils.common.constants.src.custom_logger import Colors
from libs.utils.common.constants.src.seeder import (
    INSTRUMENTS_FILE_PATH,
    SCRIPTS_FILE_PATH,
)
from libs.utils.common.custom_logger.src import CustomLogger, color_string
from libs.utils.common.enums.src.custom_logger import LogType
from libs.utils.db.postgres.operations.src import (
    InstrumentOperations,
    ScriptOperations,
)

log = CustomLogger("FastAPI Lifespan")
logger, listener = log.get_logger()
listener.start()


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    seed_result = await InstrumentOperations.seed_missing_instruments_from_file(
        INSTRUMENTS_FILE_PATH
    )
    logger.info(
        color_string(
            f"Instrument seed check complete "
            f"inserted_count: {len(seed_result['inserted_symbols'])} "
            f"skipped_count: {len(seed_result['skipped_symbols'])}",
            Colors.BOLD_GREEN,
        ),
        extra={"logType": LogType.STARTUP.value},
    )
    scripts_seed_result = await ScriptOperations.seed_missing_scripts_from_file(
        SCRIPTS_FILE_PATH
    )
    logger.info(
        color_string(
            f"Script seed check complete "
            f"inserted_count: {len(scripts_seed_result['inserted_symbols'])} "
            f"skipped_count: {len(scripts_seed_result['skipped_symbols'])}",
            Colors.BOLD_GREEN,
        ),
        extra={"logType": LogType.STARTUP.value},
    )
    yield
