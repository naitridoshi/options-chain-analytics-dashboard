from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from starlette.responses import RedirectResponse

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.date_time.src import (
    get_current_utc_timestamp,
    get_execution_time_in_readable_format,
)
from libs.utils.common.fyers_client.src import FyersClientService
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


@core_route.get("/api/v1/fyers/login")
def fyers_login(_: bool = Depends(verify_basic_auth)):
    login_url = FyersClientService.get_login_url()
    return RedirectResponse(url=login_url)


@core_route.get("/api/v1/fyers/status")
async def fyers_token_status(_: bool = Depends(verify_basic_auth)):
    data = await FyersClientService.get_today_token_status()
    return JSONResponse(status_code=200, content={"success": True, "data": data})


@core_route.get("/callback")
async def fyers_callback(
    request: Request,
    auth_code: str | None = Query(default=None),
    code: str | None = Query(default=None),
):
    resolved_auth_code = auth_code or code
    if not resolved_auth_code:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Missing auth_code/code in callback query params.",
            },
        )

    # Exchange auth code for token and store
    await FyersClientService.exchange_auth_code_and_store(resolved_auth_code)

    # Trigger immediate ingestion start
    try:
        from apps.fastapi.src.lifespan import get_app_state

        app_state = get_app_state(request.app)

        if hasattr(app_state, "_token_watcher") and app_state._token_watcher:
            await app_state._token_watcher.trigger_immediate_check()

        logger.info("FYERS token stored and ingestion triggered")

    except Exception as error:
        logger.warning(
            f"Token stored but ingestion trigger failed - error: {str(error)}"
        )
        # Don't fail the callback if trigger fails

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": "FYERS token stored successfully. Ingestion starting.",
        },
    )
