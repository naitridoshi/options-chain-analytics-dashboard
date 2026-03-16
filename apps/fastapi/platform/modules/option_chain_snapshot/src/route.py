from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
from apps.fastapi.platform.modules.option_chain_snapshot.src.service import (
    OptionChainSnapshotService,
)

snapshot_route = APIRouter(prefix="/api/v1/snapshot", tags=["Snapshot"])


@snapshot_route.post("/trigger")
async def trigger_snapshot(_: str = Depends(verify_basic_auth)):
    result = await OptionChainSnapshotService.capture_for_all_active_instruments()
    return JSONResponse(status_code=200, content={"success": True, "data": result})


@snapshot_route.get("/status")
async def snapshot_status(_: str = Depends(verify_basic_auth)):
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "data": OptionChainSnapshotService.status(),
        },
    )
