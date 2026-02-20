"""Device discovery and status routes."""

from __future__ import annotations

from fastapi import APIRouter

from cellswarm.core.device import DeviceState
from cellswarm.server.schemas import DeviceResponse, DevicesResponse, DeviceStateAPI
from cellswarm.server.state import app_state

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _device_to_response(dev) -> DeviceResponse:
    return DeviceResponse(
        serial=dev.serial,
        short_serial=dev.short_serial,
        state=DeviceStateAPI(dev.state.value),
        model=dev.model,
        total_ram_mb=dev.total_ram_mb,
        available_ram_mb=dev.available_ram_mb,
        usable_ram_mb=dev.usable_ram_mb,
        storage_free_mb=dev.storage_free_mb,
        cpu_cores=dev.cpu_cores,
        cpu_arch=dev.cpu_arch,
        android_version=dev.android_version,
        thermal_temp_c=dev.thermal_temp_c,
        has_swarm_worker=dev.has_swarm_rpc,
        models=dev.models,
    )


@router.get("", response_model=DevicesResponse)
async def get_devices():
    devices = list(app_state.device_manager.devices.values())
    ready = [d for d in devices if d.state == DeviceState.READY]
    return DevicesResponse(
        devices=[_device_to_response(d) for d in devices],
        count=len(devices),
        ready_count=len(ready),
    )


@router.post("/refresh", response_model=DevicesResponse)
async def refresh_devices():
    await app_state.refresh_devices()
    devices = list(app_state.device_manager.devices.values())
    ready = [d for d in devices if d.state == DeviceState.READY]
    return DevicesResponse(
        devices=[_device_to_response(d) for d in devices],
        count=len(devices),
        ready_count=len(ready),
    )
