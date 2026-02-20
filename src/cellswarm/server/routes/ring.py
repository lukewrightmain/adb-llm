"""Ring topology status and control routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from cellswarm.server.schemas import RingNodeResponse, RingStartRequest, RingStatusResponse
from cellswarm.server.state import app_state

router = APIRouter(prefix="/api/ring", tags=["ring"])


@router.get("/status", response_model=RingStatusResponse)
async def ring_status():
    status = await app_state.orchestrator.status()

    nodes = []
    for n in status.get("nodes", []):
        # Get device info for thermal/RAM if available
        dev = None
        if n["serial"] != "HOST":
            try:
                dev = app_state.device_manager.get_device(n["serial"])
            except Exception:
                pass

        nodes.append(RingNodeResponse(
            rank=n["rank"],
            serial=n["serial"],
            is_host=n["serial"] == "HOST",
            data_port=n["data_port"],
            running=n.get("running", False),
            thermal_temp_c=dev.thermal_temp_c if dev else None,
            available_ram_mb=dev.available_ram_mb if dev else None,
        ))

    return RingStatusResponse(
        active=status.get("active", False),
        world_size=status.get("world_size", 0),
        model=app_state._active_model,
        nodes=nodes,
    )


@router.post("/start")
async def ring_start(req: RingStartRequest):
    if (await app_state.orchestrator.status()).get("active"):
        raise HTTPException(400, "Ring already active. Stop it first.")

    try:
        await app_state.orchestrator.start(
            model_path=req.model_path,
            port=req.port,
            target_devices=req.devices,
            total_layers=req.total_layers,
            context_size=req.context_size,
            prefetch=req.prefetch,
        )
        app_state._active_model = req.model_path
        return {"status": "started", "model": req.model_path}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/stop")
async def ring_stop():
    try:
        await app_state.orchestrator.shutdown()
        app_state._active_model = None
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(500, str(e))
