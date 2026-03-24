from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
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


@index_snapshot_route.get("/heatmap")
async def get_heatmap_data(
    category: str | None = None,
    _: bool = Depends(verify_basic_auth),
):
    result = await IndexSnapshotService.get_heatmap_data(category=category)
    return JSONResponse(status_code=200, content={"success": True, "data": result})
