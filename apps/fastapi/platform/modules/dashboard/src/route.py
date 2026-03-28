from html import escape

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import RedirectResponse

from apps.fastapi.auth.src.basic_auth import (
    get_current_display_user,
    get_current_user,
    verify_basic_auth,
)
from apps.fastapi.platform.modules.dashboard.src.service import (
    OptionChainDashboardService,
)
from libs.utils.common.constants.src.templates import (
    COI_LIVE_TEMPLATE_HTML,
    DASHBOARD_TEMPLATE_HTML,
    HEATMAP_TEMPLATE_HTML,
    LOGIN_TEMPLATE_HTML,
    MARKET_BREADTH_TEMPLATE_HTML,
)

dashboard_route = APIRouter(tags=["Dashboard"])


def _render_login_template(current_user: str | None) -> str:
    display_name = escape(current_user or "")
    is_authenticated = "true" if current_user else "false"
    return LOGIN_TEMPLATE_HTML.replace("__AUTH_DISPLAY_NAME__", display_name).replace(
        "__IS_AUTHENTICATED__", is_authenticated
    )


@dashboard_route.get("/api/v1/dashboard/data")
async def dashboard_data(
    symbol: str | None = Query(default=None),
    timeline_limit: int = Query(default=100, ge=1, le=1000),
    _: str = Depends(verify_basic_auth),
):
    data = await OptionChainDashboardService.get_dashboard_data(
        symbol=symbol,
        timeline_limit=timeline_limit,
    )
    return JSONResponse(status_code=200, content={"success": True, "data": data})


@dashboard_route.get("/login", response_class=HTMLResponse)
async def fyers_login_page(request: Request):
    current_user = get_current_display_user(request)
    return HTMLResponse(_render_login_template(current_user))


@dashboard_route.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(DASHBOARD_TEMPLATE_HTML)


@dashboard_route.get("/market-breadth", response_class=HTMLResponse)
async def market_breadth_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(MARKET_BREADTH_TEMPLATE_HTML)


@dashboard_route.get("/heatmap", response_class=HTMLResponse)
async def heatmap_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(HEATMAP_TEMPLATE_HTML)


@dashboard_route.get("/coi-live", response_class=HTMLResponse)
async def coi_live_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(COI_LIVE_TEMPLATE_HTML)


@dashboard_route.get("/api/v1/coi-live/data")
async def coi_live_data(
    symbol: str | None = Query(default=None),
    _: str = Depends(verify_basic_auth),
):
    """Get COI Live data for the dashboard."""
    from apps.fastapi.platform.modules.coi_live.src.service import (
        COILiveService,
    )

    data = await COILiveService.get_coi_live_data(symbol=symbol)
    return JSONResponse(status_code=200, content={"success": True, "data": data})
