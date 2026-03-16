from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
from libs.utils.common.runtime_store.src import (
    RuntimeDashboardService,
    RuntimeStoreHealthService,
)

market_data_route = APIRouter(prefix="/api/v1/market-data", tags=["Market Data"])


@market_data_route.get("/status")
async def get_market_data_status(
    symbol: str | None = Query(default=None),
    _: str = Depends(verify_basic_auth),
) -> JSONResponse:
    payload = await RuntimeDashboardService.get_dashboard_data(
        symbol=symbol,
        timeline_limit=1,
    )
    runtime_store = await RuntimeStoreHealthService.get_status()
    latest = payload.get("latest") if payload else None
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "data": {
                "symbol": symbol,
                "runtime_store": runtime_store,
                "runtime_snapshot_available": bool(payload),
                "snapshot_source": payload.get("source") if payload else None,
                "latest_snapshot_at": latest.get("captured_at") if latest else None,
                "live_app_healthy": runtime_store.get("live_app_healthy"),
                "websocket_ready": bool(
                    runtime_store.get("healthy")
                    and runtime_store.get("live_app_healthy")
                ),
            },
        },
    )
