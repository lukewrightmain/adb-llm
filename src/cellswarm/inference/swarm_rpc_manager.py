"""Manage swarm-rpc lifecycle on Android devices."""

from __future__ import annotations

import asyncio

from loguru import logger

from cellswarm.core.config import REMOTE_SWARM_RPC, RPC_REMOTE_PORT, RPC_START_TIMEOUT
from cellswarm.core.device import DeviceInfo
from cellswarm.core.errors import RpcServerError
from cellswarm.utils.adb import adb_shell


class SwarmRpcManager:
    """Start/stop/monitor swarm-rpc on devices."""

    def __init__(self, port: int = RPC_REMOTE_PORT) -> None:
        self._port = port

    async def start(self, device: DeviceInfo, rpc_path: str | None = None) -> None:
        """Start swarm-rpc on a device via ADB shell."""
        rpc_bin = rpc_path or device.swarm_rpc_path or REMOTE_SWARM_RPC

        # Kill any existing instance
        await self.stop(device)

        # Start in background (use /data/local/tmp for logs — Android has no /tmp)
        log_path = "/data/local/tmp/swarm-rpc.log"
        cache_dir = "/data/local/tmp/cellswarm/cache"
        cmd = (
            f"cd /data/local/tmp && "
            f"export SWARM_CACHE={cache_dir} && "
            f"mkdir -p {cache_dir} && "
            f"nohup {rpc_bin} -H 0.0.0.0 -p {self._port} --cache "
            f"> {log_path} 2>&1 &"
        )
        await adb_shell(device.serial, cmd, check=False)

        # Wait for it to come up
        for _ in range(RPC_START_TIMEOUT):
            if await self.is_running(device):
                logger.info("swarm-rpc started on {} (port {})", device.serial, self._port)
                return
            await asyncio.sleep(1)

        # Grab logs for debugging
        log_output = await adb_shell(device.serial, f"cat {log_path}", check=False)
        raise RpcServerError(
            device.serial,
            f"Failed to start within {RPC_START_TIMEOUT}s. Log:\n{log_output[:500]}",
        )

    async def stop(self, device: DeviceInfo) -> None:
        """Kill swarm-rpc on a device."""
        await adb_shell(device.serial, "pkill -f swarm-rpc", check=False)
        await asyncio.sleep(0.5)
        logger.debug("swarm-rpc stopped on {}", device.serial)

    async def is_running(self, device: DeviceInfo) -> bool:
        """Check if swarm-rpc is running on a device."""
        out = await adb_shell(device.serial, "pidof swarm-rpc", check=False)
        return bool(out.strip())

    async def start_all(self, devices: list[DeviceInfo]) -> dict[str, bool]:
        """Start swarm-rpc on all devices. Returns {serial: success}."""
        results: dict[str, bool] = {}

        async def _start_one(dev: DeviceInfo) -> tuple[str, bool]:
            try:
                await self.start(dev)
                return dev.serial, True
            except Exception as e:
                logger.error("Failed to start swarm-rpc on {}: {}", dev.serial, e)
                return dev.serial, False

        raw = await asyncio.gather(*[_start_one(d) for d in devices])
        return {serial: ok for serial, ok in raw}

    async def stop_all(self, devices: list[DeviceInfo]) -> None:
        """Stop swarm-rpc on all devices."""
        await asyncio.gather(*[self.stop(d) for d in devices])
