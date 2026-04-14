from html import escape

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import RedirectResponse

from apps.fastapi.auth.src.basic_auth import (
    get_current_display_user,
    get_current_user,
    verify_basic_auth,
)
from apps.fastapi.platform.modules.dashboard.src.most_active_service import (
    MostActiveService,
)
from apps.fastapi.platform.modules.dashboard.src.service import (
    OptionChainDashboardService,
)
from libs.utils.common.constants.src.templates import (
    COI_LIVE_TEMPLATE_HTML,
    COI_PCR_LIVE_TEMPLATE_HTML,
    DASHBOARD_TEMPLATE_HTML,
    HEATMAP_TEMPLATE_HTML,
    INDEX_SCRIPTS_TEMPLATE_HTML,
    LOGIN_TEMPLATE_HTML,
    MARKET_BREADTH_TEMPLATE_HTML,
    MOST_ACTIVE_TEMPLATE_HTML,
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
    try:
        data = await OptionChainDashboardService.get_dashboard_data(
            symbol=symbol,
            timeline_limit=timeline_limit,
        )
        return JSONResponse(status_code=200, content={"success": True, "data": data})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
        )


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

    try:
        data = await COILiveService.get_coi_live_data(symbol=symbol)
        return JSONResponse(status_code=200, content={"success": True, "data": data})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@dashboard_route.get("/coi-pcr-live", response_class=HTMLResponse)
async def coi_pcr_live_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(COI_PCR_LIVE_TEMPLATE_HTML)


@dashboard_route.get("/api/v1/coi-pcr-live/data")
async def coi_pcr_live_data(
    symbol: str | None = Query(default=None),
    _: str = Depends(verify_basic_auth),
):
    """Get COI PCR Live data for the dashboard."""
    from apps.fastapi.platform.modules.coi_live.src.pcr_service import (
        COIPCRLiveService,
    )

    try:
        data = await COIPCRLiveService.get_coi_pcr_live_data(symbol=symbol)
        return JSONResponse(status_code=200, content={"success": True, "data": data})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@dashboard_route.get("/most-active", response_class=HTMLResponse)
async def most_active_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(MOST_ACTIVE_TEMPLATE_HTML)


@dashboard_route.get("/api/v1/most-active/data")
async def most_active_data(
    _: str = Depends(verify_basic_auth),
):
    try:
        data = await MostActiveService.get_most_active_data()
        return JSONResponse(status_code=200, content={"success": True, "data": data})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@dashboard_route.get("/index-scripts", response_class=HTMLResponse)
async def index_scripts_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(INDEX_SCRIPTS_TEMPLATE_HTML)
