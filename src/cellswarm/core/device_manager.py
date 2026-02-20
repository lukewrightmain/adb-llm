"""Device discovery, probing, and health management."""

from __future__ import annotations

import asyncio

from loguru import logger

from cellswarm.core.config import REMOTE_BASE, REMOTE_SWARM_RPC
from cellswarm.core.device import DeviceInfo, DeviceState
from cellswarm.core.errors import DeviceNotFoundError
from cellswarm.utils.adb import adb_devices, adb_shell


class DeviceManager:
    """Discovers and probes ADB-connected devices."""

    def __init__(self) -> None:
        self.devices: dict[str, DeviceInfo] = {}

    async def discover(self) -> list[DeviceInfo]:
        """Scan for all connected ADB devices."""
        raw = await adb_devices()
        self.devices.clear()
        for d in raw:
            state = DeviceState.CONNECTED if d.state == "device" else DeviceState.OFFLINE
            self.devices[d.serial] = DeviceInfo(
                serial=d.serial,
                state=state,
                usb=d.usb,
                product=d.product,
                model=d.model,
                transport_id=d.transport_id,
            )
        logger.info("Discovered {} devices", len(self.devices))
        return list(self.devices.values())

    async def probe(self, serial: str) -> DeviceInfo:
        """Deep-probe a single device for capabilities."""
        if serial not in self.devices:
            raise DeviceNotFoundError(serial)

        dev = self.devices[serial]
        if dev.state == DeviceState.OFFLINE:
            return dev

        try:
            results = await asyncio.gather(
                adb_shell(serial, "getprop ro.product.model", check=False),
                adb_shell(serial, "cat /proc/meminfo", check=False),
                adb_shell(serial, "df /data", check=False),
                adb_shell(serial, "nproc", check=False),
                adb_shell(serial, "getprop ro.product.cpu.abi", check=False),
                adb_shell(serial, "getprop ro.build.version.release", check=False),
                adb_shell(serial, "cat /sys/class/thermal/thermal_zone0/temp", check=False),
                adb_shell(serial, f"ls {REMOTE_SWARM_RPC}", check=False),
                adb_shell(serial, f"ls {REMOTE_BASE}/models/", check=False),
            )

            dev.model = results[0].strip() or dev.model
            dev.total_ram_mb, dev.available_ram_mb = _parse_meminfo(results[1])
            dev.storage_free_mb = _parse_df(results[2])
            dev.cpu_cores = _parse_int(results[3], 0)
            dev.cpu_arch = results[4].strip()
            dev.android_version = results[5].strip()
            dev.thermal_temp_c = _parse_thermal(results[6])
            dev.has_swarm_rpc = "No such file" not in results[7] and results[7].strip() != ""
            if dev.has_swarm_rpc:
                dev.swarm_rpc_path = REMOTE_SWARM_RPC
            dev.models = _parse_model_list(results[8])
            dev.state = DeviceState.READY

        except Exception as e:
            logger.error("Probe failed for {}: {}", serial, e)
            dev.state = DeviceState.ERROR

        return dev

    async def probe_all(self) -> list[DeviceInfo]:
        """Probe all discovered devices in parallel."""
        tasks = [self.probe(serial) for serial in self.devices]
        return list(await asyncio.gather(*tasks))

    async def get_ready_devices(self) -> list[DeviceInfo]:
        """Return only devices in READY state."""
        return [d for d in self.devices.values() if d.state == DeviceState.READY]

    def get_device(self, serial: str) -> DeviceInfo:
        """Get device by serial, raise if not found."""
        if serial not in self.devices:
            raise DeviceNotFoundError(serial)
        return self.devices[serial]

    def resolve_targets(self, target: str) -> list[DeviceInfo]:
        """Resolve 'all' or comma-separated serials to device list."""
        if target.lower() == "all":
            return [d for d in self.devices.values() if d.state != DeviceState.OFFLINE]
        serials = [s.strip() for s in target.split(",")]
        result: list[DeviceInfo] = []
        for s in serials:
            if s not in self.devices:
                raise DeviceNotFoundError(s)
            result.append(self.devices[s])
        return result


def _parse_meminfo(raw: str) -> tuple[int, int]:
    """Parse /proc/meminfo, return (total_mb, available_mb)."""
    total = available = 0
    for line in raw.splitlines():
        if line.startswith("MemTotal:"):
            total = _parse_kb_value(line) // 1024
        elif line.startswith("MemAvailable:"):
            available = _parse_kb_value(line) // 1024
    return total, available


def _parse_kb_value(line: str) -> int:
    """Extract kB value from meminfo line like 'MemTotal:    12345 kB'."""
    parts = line.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return 0


def _parse_df(raw: str) -> int:
    """Parse `df /data` output, return free space in MB."""
    lines = raw.strip().splitlines()
    if len(lines) < 2:
        return 0
    parts = lines[1].split()
    # df output: Filesystem 1K-blocks Used Available Use% Mounted
    if len(parts) >= 4:
        try:
            return int(parts[3]) // 1024  # Convert 1K-blocks to MB
        except ValueError:
            pass
    return 0


def _parse_int(raw: str, default: int = 0) -> int:
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return default


def _parse_thermal(raw: str) -> float:
    """Parse thermal zone temp (millidegrees -> degrees)."""
    try:
        val = int(raw.strip())
        return val / 1000.0 if val > 1000 else float(val)
    except (ValueError, AttributeError):
        return 0.0


def _parse_model_list(raw: str) -> list[str]:
    """Parse `ls` output of model directory."""
    if "No such file" in raw or not raw.strip():
        return []
    return [f.strip() for f in raw.strip().splitlines() if f.strip()]
