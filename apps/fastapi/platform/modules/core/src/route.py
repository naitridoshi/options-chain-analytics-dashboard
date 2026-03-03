from fastapi import APIRouter
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from starlette.responses import RedirectResponse

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.date_time.src import (
    get_current_utc_timestamp,
    get_execution_time_in_readable_format,
)
from libs.utils.config.src.fastapi import FASTAPI_APP_ENVIRONMENT

log = CustomLogger("BackendCoreRoute")

logger, listener = log.get_logger()
listener.start()

core_route = APIRouter(tags=["Core Routes"])
auth_scheme = HTTPBearer()
start_time = get_current_utc_timestamp()


@core_route.get("/")
def redirect_to_health():
    return RedirectResponse(url="/health")


@core_route.get("/health")
def root():
    logger.info("Backend app health endpoint accessed")
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "environment": FASTAPI_APP_ENVIRONMENT,
            "uptime": get_execution_time_in_readable_format(start_time=start_time),
        },
    )
