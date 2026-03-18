from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
from apps.fastapi.platform.modules.script_snapshot.src.service import (
    ScriptSnapshotService,
)

script_snapshot_route = APIRouter(
    prefix="/api/v1/script-snapshot", tags=["Script Snapshot"]
)


@script_snapshot_route.post("/trigger")
async def trigger_script_snapshot(_: bool = Depends(verify_basic_auth)):
    result = await ScriptSnapshotService.capture_for_all_active_scripts()
    return JSONResponse(status_code=200, content={"success": True, "data": result})


@script_snapshot_route.get("/status")
async def script_snapshot_status(_: bool = Depends(verify_basic_auth)):
    return JSONResponse(
        status_code=200,
        content={"success": True, "data": ScriptSnapshotService.status()},
    )


@script_snapshot_route.get("/advance-decline")
async def script_advance_decline(_: bool = Depends(verify_basic_auth)):
    result = await ScriptSnapshotService.get_advance_decline()
    return JSONResponse(status_code=200, content={"success": True, "data": result})
