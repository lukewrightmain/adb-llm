"""Tunnel and device health monitoring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger

from cellswarm.core.device import DeviceInfo, DeviceState
from cellswarm.transport.base import TransportLayer
from cellswarm.utils.adb import adb_devices, adb_shell


@dataclass
class HealthReport:
    serial: str
    usb_connected: bool
    swarm_rpc_running: bool
    tunnel_alive: bool
    thermal_temp_c: float
    status: str  # "healthy", "degraded", "down"


class HealthMonitor:
    """Monitors device connectivity, swarm-rpc, and tunnel health."""

    def __init__(self, transport: TransportLayer) -> None:
        self._transport = transport
        self._running = False
        self._task: asyncio.Task | None = None

    async def check_device(self, device: DeviceInfo) -> HealthReport:
        """Run all health checks on a single device."""
        usb_ok = False
        rpc_ok = False
        tunnel_ok = False
        temp = 0.0

        # 1. USB connected?
        try:
            raw_devices = await adb_devices()
            serials = {d.serial: d.state for d in raw_devices}
            usb_ok = serials.get(device.serial) == "device"
        except Exception:
            pass

        if usb_ok:
            # 2. swarm-rpc running?
            try:
                out = await adb_shell(device.serial, "pgrep -f swarm-rpc", check=False)
                rpc_ok = bool(out.strip())
            except Exception:
                pass

            # 3. Thermal
            try:
                out = await adb_shell(device.serial, "cat /sys/class/thermal/thermal_zone0/temp", check=False)
                val = int(out.strip())
                temp = val / 1000.0 if val > 1000 else float(val)
            except Exception:
                pass

        # 4. Tunnel alive?
        try:
            tunnel_ok = await self._transport.health_check(device)
        except Exception:
            pass

        if usb_ok and rpc_ok and tunnel_ok:
            status = "healthy"
        elif usb_ok:
            status = "degraded"
        else:
            status = "down"

        return HealthReport(
            serial=device.serial,
            usb_connected=usb_ok,
            swarm_rpc_running=rpc_ok,
            tunnel_alive=tunnel_ok,
            thermal_temp_c=temp,
            status=status,
        )

    async def check_all(self, devices: list[DeviceInfo]) -> list[HealthReport]:
        """Check health of all devices in parallel."""
        return await asyncio.gather(*[self.check_device(d) for d in devices])

    async def _monitor_loop(
        self,
        devices: list[DeviceInfo],
        interval: float,
        on_unhealthy: callable | None = None,
    ) -> None:
        """Continuous monitoring loop."""
        while self._running:
            reports = await self.check_all(devices)
            for report in reports:
                if report.status == "down":
                    logger.warning("Device {} is DOWN", report.serial)
                    if on_unhealthy:
                        on_unhealthy(report)
                elif report.status == "degraded":
                    logger.warning("Device {} degraded (rpc={}, tunnel={})",
                                   report.serial, report.swarm_rpc_running, report.tunnel_alive)
                if report.thermal_temp_c > 42:
                    logger.warning("Device {} thermal: {:.0f}C", report.serial, report.thermal_temp_c)
            await asyncio.sleep(interval)

    def start(
        self,
        devices: list[DeviceInfo],
        interval: float = 5.0,
        on_unhealthy: callable | None = None,
    ) -> None:
        """Start background health monitoring."""
        self._running = True
        self._task = asyncio.ensure_future(
            self._monitor_loop(devices, interval, on_unhealthy)
        )

    def stop(self) -> None:
        """Stop background monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
