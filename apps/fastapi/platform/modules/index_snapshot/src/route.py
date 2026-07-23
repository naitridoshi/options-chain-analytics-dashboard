from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
from apps.fastapi.platform.modules.index_snapshot.src.constituent_service import (
    ConstituentSnapshotService,
)
from apps.fastapi.platform.modules.index_snapshot.src.service import (
    IndexSnapshotService,
)

index_snapshot_route = APIRouter(
    prefix="/api/v1/index-snapshot", tags=["Index Snapshot"]
)


@index_snapshot_route.post("/trigger")
async def trigger_index_snapshot(_: bool = Depends(verify_basic_auth)):
    result = await IndexSnapshotService.capture_for_all_active_indices()
    return JSONResponse(status_code=200, content={"success": True, "data": result})


@index_snapshot_route.get("/status")
async def index_snapshot_status(_: bool = Depends(verify_basic_auth)):
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": IndexSnapshotService.status()},
    )


@index_snapshot_route.post("/constituents/trigger")
async def trigger_constituent_snapshot(_: bool = Depends(verify_basic_auth)):
    result = await ConstituentSnapshotService.capture_for_all_constituents()
    return JSONResponse(status_code=200, content={"success": True, "data": result})


@index_snapshot_route.get("/constituents/status")
async def constituent_snapshot_status(_: bool = Depends(verify_basic_auth)):
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": ConstituentSnapshotService.status()},
    )


@index_snapshot_route.get("/heatmap")
async def get_heatmap_data(
    category: str | None = None,
    _: bool = Depends(verify_basic_auth),
):
    result = await IndexSnapshotService.get_heatmap_data(category=category)
    return JSONResponse(status_code=200, content={"success": True, "data": result})


@index_snapshot_route.get("/breadth-summary")
async def get_breadth_summary(_: bool = Depends(verify_basic_auth)):
    result = await IndexSnapshotService.get_breadth_summary()
    return JSONResponse(status_code=200, content={"success": True, "data": result})


@index_snapshot_route.get("/constituents")
async def get_constituents(
    index: str = Query(default=None),
    _: bool = Depends(verify_basic_auth),
):
    """Get constituent scripts for a specific index/sector."""
    if not index:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "index parameter is required"},
        )
    try:
        data = await ConstituentSnapshotService.get_constituents_for_index(index)
        return JSONResponse(status_code=200, content={"success": True, "data": data})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )
