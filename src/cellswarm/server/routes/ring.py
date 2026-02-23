"""Ring topology status and control routes."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, HTTPException
from loguru import logger

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
        draft_model=app_state._active_draft_model,
        nodes=nodes,
    )


async def _run_ring_start(req: RingStartRequest) -> None:
    """Background coroutine that runs the blocking orchestrator.start()."""
    try:
        app_state._launch_status = "running"
        app_state._launch_message = "Discovering devices..."
        app_state._launch_error = None

        await app_state.orchestrator.start(
            model_path=req.model_path,
            port=req.port,
            target_devices=req.devices,
            total_layers=req.total_layers,
            context_size=req.context_size,
            prefetch=req.prefetch,
            draft_model_path=req.draft_model_path,
            draft_max=req.draft_max,
        )
        app_state._active_model = req.model_path
        app_state._active_draft_model = req.draft_model_path
        app_state._launch_status = "done"
        app_state._launch_message = "Ring started successfully"
        logger.info("Ring start completed: {}", req.model_path)
    except Exception as e:
        app_state._launch_status = "failed"
        app_state._launch_error = str(e)
        app_state._launch_message = f"Failed: {e}"
        logger.error("Ring start failed: {}", e)


@router.post("/start")
async def ring_start(req: RingStartRequest):
    if (await app_state.orchestrator.status()).get("active"):
        raise HTTPException(400, "Ring already active. Stop it first.")

    if app_state._launch_status == "running":
        raise HTTPException(400, "Ring launch already in progress.")

    # Launch in background task — return immediately so the browser doesn't timeout
    app_state._launch_task = asyncio.create_task(_run_ring_start(req))
    return {"status": "launching", "message": "Ring start initiated"}


@router.get("/launch-status")
async def ring_launch_status():
    """Progress endpoint polled by the dashboard during ring startup."""
    return {
        "status": app_state._launch_status,
        "message": app_state._launch_message,
        "error": app_state._launch_error,
    }


@router.post("/stop")
async def ring_stop():
    try:
        await app_state.orchestrator.shutdown()
        app_state._active_model = None
        app_state._active_draft_model = None
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/health")
async def ring_health():
    """Check if cellswarm-server is ready to accept inference requests."""
    status = await app_state.orchestrator.status()
    if not status.get("active"):
        return {"ready": False, "status": "inactive"}

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://127.0.0.1:8080/health")
            if resp.status_code == 200:
                return {"ready": True, "status": "ready"}
            return {"ready": False, "status": "loading"}
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
        return {"ready": False, "status": "loading"}
