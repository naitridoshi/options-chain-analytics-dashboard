from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
from apps.fastapi.platform.modules.dashboard.src.service import (
    OptionChainDashboardService,
)
from libs.utils.common.constants.src.templates import (
    DASHBOARD_TEMPLATE_HTML,
    LOGIN_TEMPLATE_HTML,
)

dashboard_route = APIRouter(tags=["Dashboard"])


@dashboard_route.get("/api/v1/dashboard/data")
async def dashboard_data(
    symbol: str | None = Query(default=None),
    timeline_limit: int = Query(default=100, ge=1, le=1000),
    _: bool = Depends(verify_basic_auth),
):
    data = await OptionChainDashboardService.get_dashboard_data(
        symbol=symbol,
        timeline_limit=timeline_limit,
    )
    return JSONResponse(status_code=200, content={"success": True, "data": data})


@dashboard_route.get("/login", response_class=HTMLResponse)
async def fyers_login_page(_: bool = Depends(verify_basic_auth)):
    return HTMLResponse(LOGIN_TEMPLATE_HTML)


@dashboard_route.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(_: bool = Depends(verify_basic_auth)):
    return HTMLResponse(DASHBOARD_TEMPLATE_HTML)
