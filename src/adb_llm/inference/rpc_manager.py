"""Manage rpc-server lifecycle on Android devices."""

from __future__ import annotations

import asyncio

from loguru import logger

from adb_llm.core.config import REMOTE_RPC_SERVER, RPC_REMOTE_PORT, RPC_START_TIMEOUT
from adb_llm.core.device import DeviceInfo
from adb_llm.core.errors import RpcServerError
from adb_llm.utils.adb import adb_shell


class RpcManager:
    """Start/stop/monitor rpc-server on devices."""

    def __init__(self, port: int = RPC_REMOTE_PORT) -> None:
        self._port = port

    async def start(self, device: DeviceInfo, rpc_path: str | None = None) -> None:
        """Start rpc-server on a device via ADB shell."""
        rpc_bin = rpc_path or device.rpc_server_path or REMOTE_RPC_SERVER

        # Kill any existing instance
        await self.stop(device)

        # Start in background (use /data/local/tmp for logs — Android has no /tmp)
        log_path = "/data/local/tmp/rpc-server.log"
        cache_dir = "/data/local/tmp/adb-llm/cache"
        cmd = (
            f"cd /data/local/tmp && "
            f"export LLAMA_CACHE={cache_dir} && "
            f"mkdir -p {cache_dir} && "
            f"nohup {rpc_bin} -H 0.0.0.0 -p {self._port} --cache "
            f"> {log_path} 2>&1 &"
        )
        await adb_shell(device.serial, cmd, check=False)

        # Wait for it to come up
        for _ in range(RPC_START_TIMEOUT):
            if await self.is_running(device):
                logger.info("rpc-server started on {} (port {})", device.serial, self._port)
                return
            await asyncio.sleep(1)

        # Grab logs for debugging
        log_output = await adb_shell(device.serial, f"cat {log_path}", check=False)
        raise RpcServerError(
            device.serial,
            f"Failed to start within {RPC_START_TIMEOUT}s. Log:\n{log_output[:500]}",
        )

    async def stop(self, device: DeviceInfo) -> None:
        """Kill rpc-server on a device."""
        await adb_shell(device.serial, "pkill -f rpc-server", check=False)
        await asyncio.sleep(0.5)
        logger.debug("rpc-server stopped on {}", device.serial)

    async def is_running(self, device: DeviceInfo) -> bool:
        """Check if rpc-server is running on a device."""
        out = await adb_shell(device.serial, "pidof rpc-server", check=False)
        return bool(out.strip())

    async def start_all(self, devices: list[DeviceInfo]) -> dict[str, bool]:
        """Start rpc-server on all devices. Returns {serial: success}."""
        results: dict[str, bool] = {}

        async def _start_one(dev: DeviceInfo) -> tuple[str, bool]:
            try:
                await self.start(dev)
                return dev.serial, True
            except Exception as e:
                logger.error("Failed to start rpc-server on {}: {}", dev.serial, e)
                return dev.serial, False

        raw = await asyncio.gather(*[_start_one(d) for d in devices])
        return {serial: ok for serial, ok in raw}

    async def stop_all(self, devices: list[DeviceInfo]) -> None:
        """Stop rpc-server on all devices."""
        await asyncio.gather(*[self.stop(d) for d in devices])
