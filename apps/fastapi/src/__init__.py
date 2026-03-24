import subprocess

import uvicorn
from fastapi import FastAPI

from apps.fastapi.auth.src import middlewares
from apps.fastapi.platform.modules.core.src import core_route
from apps.fastapi.platform.modules.dashboard.src import dashboard_route
from apps.fastapi.platform.modules.index_snapshot.src import (
    index_snapshot_route,
)
from apps.fastapi.platform.modules.market_data.src import (
    market_data_route,
    market_data_ws_route,
)
from apps.fastapi.platform.modules.option_chain_snapshot.src import (
    snapshot_route,
)
from apps.fastapi.platform.modules.script_snapshot.src import (
    script_snapshot_route,
)
from apps.fastapi.src.lifespan import app_lifespan
from libs.utils.common.custom_logger.src import (
    Colors,
    CustomLogger,
    LogType,
    color_string,
)
from libs.utils.common.os_helpers.src import BASE_DIR
from libs.utils.config.src.fastapi import GUNICORN_CONFIG_PATH

log = CustomLogger(
    "Options Chain Analytics Dashboard Backend", queue_logger=True, is_request=False
)
logger, listener = log.get_logger()
listener.start()

app = FastAPI(
    title="Options Chain Analytics Dashboard Backend",
    description="Real-time options chain data via Fyers API websocket streaming.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    middleware=middlewares,
    lifespan=app_lifespan,
)


app.include_router(core_route)
app.include_router(snapshot_route)
app.include_router(market_data_route)
app.include_router(market_data_ws_route)
app.include_router(script_snapshot_route)
app.include_router(index_snapshot_route)
app.include_router(dashboard_route)


def start_server(
    host: str,
    port: int,
    reload: bool = True,
    workers: int = 8,
    threads: int = 10,
    environment: str = "development",
):
    if environment == "development":
        logger.info(
            color_string(
                f"Starting server on http://{host}:{port}/docs with "
                f"{workers} workers, environment: {environment}, "
                f"reload: {reload}.",
                Colors.BOLD_RED,
            ),
            extra={"logType": LogType.STARTUP.value},
        )
        uvicorn.run(
            "apps.fastapi.src:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info",
            workers=workers,
        )
    else:
        logger.info(
            color_string(
                f"Deploying server on http://{host}:{port}/docs with "
                f"{workers} workers, {threads} threads",
                Colors.BOLD_RED,
            ),
            extra={"logType": LogType.STARTUP.value},
        )
        subprocess.run(
            [
                "gunicorn",
                "-c",
                f"{BASE_DIR}/{GUNICORN_CONFIG_PATH}",
                "apps.fastapi.src:app",
            ]
        )
