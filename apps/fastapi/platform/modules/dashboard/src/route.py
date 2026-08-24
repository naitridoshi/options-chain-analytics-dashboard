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
    CHART_TEMPLATE_HTML,
    COI_LIVE_TEMPLATE_HTML,
    COI_PCR_LIVE_TEMPLATE_HTML,
    DASHBOARD_TEMPLATE_HTML,
    HEATMAP_TEMPLATE_HTML,
    HISTORICAL_SCORING_TEMPLATE_HTML,
    INDEX_SCRIPTS_TEMPLATE_HTML,
    LOGIN_TEMPLATE_HTML,
    MARKET_BREADTH_TEMPLATE_HTML,
    MOST_ACTIVE_TEMPLATE_HTML,
    SCORING_TEMPLATE_HTML,
)
from libs.utils.config.src.auth import ADMIN_DISPLAY_NAME
from libs.utils.db.redis.src import (
    RedisLiveMarketStore,
    RedisOptionChainSnapshotStore,
)

dashboard_route = APIRouter(tags=["Dashboard"])


def _render_login_template(current_user: str | None) -> str:
    display_name = escape(current_user or "")
    is_authenticated = "true" if current_user else "false"
    return (
        LOGIN_TEMPLATE_HTML.replace("__AUTH_DISPLAY_NAME__", display_name)
        .replace("__IS_AUTHENTICATED__", is_authenticated)
        .replace("__CURRENT_DISPLAY_NAME__", display_name)
        .replace("__ADMIN_DISPLAY_NAME__", escape(ADMIN_DISPLAY_NAME))
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


@dashboard_route.get("/api/v1/spot-data")
async def spot_data(
    _: str = Depends(verify_basic_auth),
):
    """Lightweight endpoint returning only NIFTY spot/change values."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
    symbol = "NIFTY"
    trade_date = datetime.now(IST).date().isoformat()

    try:
        live_underlying = await RedisLiveMarketStore.get_live_underlying(symbol)

        spot_price = None
        change_from_prev_close = None
        change_pct_from_prev_close = None

        if live_underlying:
            spot_price = live_underlying.get("spot_price")
            change_from_prev_close = live_underlying.get("change_from_prev_close")
            change_pct_from_prev_close = live_underlying.get(
                "change_pct_from_prev_close"
            )

        # Fallback: compute change from snapshot + previous day if live data missing
        if change_from_prev_close is None and spot_price is not None:
            prev_close = (
                live_underlying.get("prev_close_spot") if live_underlying else None
            )
            if prev_close is None:
                prev_snapshot = (
                    await RedisOptionChainSnapshotStore.get_previous_day_final_snapshot(
                        symbol
                    )
                )
                if prev_snapshot:
                    prev_latest = prev_snapshot.get("latest") or {}
                    prev_close = prev_latest.get("spot_price")
            if prev_close is not None and float(prev_close) != 0:
                change_from_prev_close = float(spot_price) - float(prev_close)
                change_pct_from_prev_close = (
                    change_from_prev_close / float(prev_close)
                ) * 100
        elif spot_price is None:
            # No live data at all - try latest snapshot
            latest_snapshot = await RedisOptionChainSnapshotStore.get_latest_snapshot(
                instrument_symbol=symbol, trade_date=trade_date
            )
            if latest_snapshot:
                latest = latest_snapshot.get("latest") or {}
                spot_price = latest.get("spot_price")
                change_from_prev_close = latest.get("change_from_prev_close")
                change_pct_from_prev_close = latest.get("change_pct_from_prev_close")

                # If still missing change, compute from previous day
                if change_from_prev_close is None and spot_price is not None:
                    prev_snapshot = await RedisOptionChainSnapshotStore.get_previous_day_final_snapshot(
                        symbol
                    )
                    if prev_snapshot:
                        prev_latest = prev_snapshot.get("latest") or {}
                        prev_close = prev_latest.get("spot_price")
                        if prev_close is not None and float(prev_close) != 0:
                            change_from_prev_close = float(spot_price) - float(
                                prev_close
                            )
                            change_pct_from_prev_close = (
                                change_from_prev_close / float(prev_close)
                            ) * 100

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": {
                    "spot_price": spot_price,
                    "change_from_prev_close": change_from_prev_close,
                    "change_pct_from_prev_close": change_pct_from_prev_close,
                },
            },
        )
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
    display_name = get_current_display_user(request) or ""
    html = DASHBOARD_TEMPLATE_HTML.replace(
        "__CURRENT_DISPLAY_NAME__", escape(display_name)
    ).replace("__ADMIN_DISPLAY_NAME__", escape(ADMIN_DISPLAY_NAME))
    return HTMLResponse(html)


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


@dashboard_route.get("/scoring", response_class=HTMLResponse)
async def scoring_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(SCORING_TEMPLATE_HTML)


@dashboard_route.get("/historical-scoring", response_class=HTMLResponse)
async def historical_scoring_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(HISTORICAL_SCORING_TEMPLATE_HTML)


@dashboard_route.get("/api/v1/historical-scoring/data")
async def historical_scoring_data(
    symbol: str | None = Query(default=None),
    _: str = Depends(verify_basic_auth),
):
    from apps.fastapi.platform.modules.historical_scoring.src.service import (
        HistoricalScoringService,
    )

    try:
        data = await HistoricalScoringService.get_historical_scoring_data(symbol=symbol)
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


@dashboard_route.get("/chart", response_class=HTMLResponse)
async def chart_page(request: Request):
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(CHART_TEMPLATE_HTML)


@dashboard_route.get("/api/v1/dashboard/chart-data")
async def chart_data(
    symbol: str | None = Query(default=None),
    call_strike: float | None = Query(default=None),
    put_strike: float | None = Query(default=None),
    _: str = Depends(verify_basic_auth),
):
    from libs.utils.common.runtime_store.src.dashboard_service import (
        RuntimeDashboardService,
    )

    try:
        data = await RuntimeDashboardService.get_chart_data(
            symbol=symbol,
            call_strike=call_strike,
            put_strike=put_strike,
        )
        return JSONResponse(status_code=200, content={"success": True, "data": data})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )
