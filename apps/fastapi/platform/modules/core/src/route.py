from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import JSONResponse
from starlette.responses import RedirectResponse

from apps.fastapi.auth.src.basic_auth import (
    authenticate_credentials,
    clear_authenticated_session,
    create_authenticated_session,
    get_current_user,
    verify_basic_auth,
)
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.date_time.src import (
    get_current_utc_timestamp,
    get_execution_time_in_readable_format,
)
from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.common.runtime_store.src import (
    RuntimeStoreHealthService,
    RuntimeWebSocketTicketService,
)
from libs.utils.config.src.fastapi import FASTAPI_APP_ENVIRONMENT

log = CustomLogger("BackendCoreRoute")

logger, listener = log.get_logger()
listener.start()

core_route = APIRouter(tags=["Core Routes"])
start_time = get_current_utc_timestamp()


@core_route.get("/")
def redirect_to_health():
    return RedirectResponse(url="/health")


@core_route.get("/health")
async def root():
    logger.info("Backend app health endpoint accessed")
    runtime_store_status = await RuntimeStoreHealthService.get_status()
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "environment": FASTAPI_APP_ENVIRONMENT,
            "uptime": get_execution_time_in_readable_format(start_time=start_time),
            "runtime_store": runtime_store_status,
        },
    )


@core_route.get("/api/v1/fyers/login")
def fyers_login(request: Request):
    if not get_current_user(request):
        return RedirectResponse(
            url=f"/login?{urlencode({'error': 'auth_required'})}",
            status_code=303,
        )
    login_url = FyersClientService.get_login_url()
    return RedirectResponse(url=login_url)


@core_route.get("/api/v1/fyers/status")
async def fyers_token_status(_: str = Depends(verify_basic_auth)):
    data = await FyersClientService.get_today_token_status()
    return JSONResponse(status_code=200, content={"success": True, "data": data})


@core_route.get("/api/v1/runtime-store/status")
async def runtime_store_status(_: str = Depends(verify_basic_auth)):
    data = await RuntimeStoreHealthService.get_status()
    return JSONResponse(status_code=200, content={"success": True, "data": data})


@core_route.post("/api/v1/market-data/ws-ticket")
async def issue_market_data_ws_ticket(
    symbol: str | None = Query(default=None),
    _: str = Depends(verify_basic_auth),
):
    normalized_symbol = (symbol or "").strip().upper()
    if not normalized_symbol:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Missing symbol query parameter"},
        )
    ticket = await RuntimeWebSocketTicketService.create_ticket(
        subject="dashboard",
        symbol=normalized_symbol,
    )
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": {"ticket": ticket}},
    )


@core_route.post("/login")
async def session_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not authenticate_credentials(username, password):
        return RedirectResponse(
            url=f"/login?{urlencode({'error': 'invalid_credentials'})}",
            status_code=303,
        )

    create_authenticated_session(request, username)
    return RedirectResponse(url="/dashboard", status_code=303)


@core_route.post("/logout")
async def session_logout(request: Request):
    clear_authenticated_session(request)
    return RedirectResponse(url="/login", status_code=303)


@core_route.get("/callback")
async def fyers_callback(
    request: Request,
    auth_code: str | None = Query(default=None),
    code: str | None = Query(default=None),
):
    if not get_current_user(request):
        return RedirectResponse(
            url=f"/login?{urlencode({'error': 'auth_required'})}",
            status_code=303,
        )

    resolved_auth_code = auth_code or code
    if not resolved_auth_code:
        return RedirectResponse(
            url=f"/login?{urlencode({'error': 'missing_auth_code'})}",
            status_code=303,
        )

    try:
        await FyersClientService.exchange_auth_code_and_store(resolved_auth_code)
    except Exception as error:
        logger.error(f"FYERS callback failed: {error}")
        return RedirectResponse(
            url=f"/login?{urlencode({'error': 'login_failed'})}",
            status_code=303,
        )

    return RedirectResponse(url="/dashboard", status_code=303)
