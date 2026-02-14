"""TCP tunneling transport via `adb forward` + SSH tunnels.

Architecture:
  Phone:60000 (rpc-server)
    -> adb forward on Windows PC: WinPC:600XX -> Phone:60000
    -> SSH tunnel from this server: localhost:600XX -> WinPC:600XX

This gives llama-server (running on this Linux server) localhost endpoints
that reach rpc-server on each phone, routed through Windows PC over USB.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from adb_llm.core.config import RPC_BASE_LOCAL_PORT, RPC_REMOTE_PORT
from adb_llm.core.device import DeviceInfo
from adb_llm.core.errors import TunnelError
from adb_llm.transport.base import TransportConnection, TransportLayer
from adb_llm.utils.adb import adb_forward, adb_forward_remove, ssh_tunnel


class AdbForwardTransport(TransportLayer):
    """Transport via `adb forward` on Windows + SSH tunnels to this server.

    For each device:
    1. `adb forward tcp:600XX tcp:60000` on Windows (USB -> phone)
    2. `ssh -L 600XX:127.0.0.1:600XX winpc` (this server -> Windows)

    Result: localhost:600XX on this server reaches rpc-server on the phone.
    """

    def __init__(self, base_port: int = RPC_BASE_LOCAL_PORT) -> None:
        self._base_port = base_port
        self._connections: dict[str, TransportConnection] = {}  # serial -> conn
        self._ssh_tunnels: dict[str, asyncio.subprocess.Process] = {}  # serial -> SSH proc
        self._next_port = base_port

    async def connect(
        self, device: DeviceInfo, remote_port: int = RPC_REMOTE_PORT
    ) -> TransportConnection:
        if device.serial in self._connections:
            return self._connections[device.serial]

        local_port = self._next_port
        self._next_port += 1

        try:
            # Step 1: adb forward on Windows PC
            await adb_forward(device.serial, local_port, remote_port)

            # Step 2: SSH tunnel from this server to Windows PC
            proc = await ssh_tunnel(local_port, local_port)
            if proc:
                self._ssh_tunnels[device.serial] = proc

        except Exception as e:
            raise TunnelError(device.serial, str(e)) from e

        conn = TransportConnection(
            device=device,
            local_port=local_port,
            remote_port=remote_port,
        )
        self._connections[device.serial] = conn
        logger.info("Full tunnel: localhost:{} -> {} -> {}:{}",
                     local_port, device.serial, device.serial, remote_port)
        return conn

    async def disconnect(self, device: DeviceInfo) -> None:
        conn = self._connections.pop(device.serial, None)
        if conn:
            # Kill SSH tunnel
            proc = self._ssh_tunnels.pop(device.serial, None)
            if proc and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()

            # Remove adb forward on Windows
            await adb_forward_remove(device.serial, conn.local_port)
            conn.active = False
            logger.info("Removed tunnel for {}", device.serial)

    async def disconnect_all(self) -> None:
        for serial in list(self._connections):
            conn = self._connections.pop(serial)

            # Kill SSH tunnel
            proc = self._ssh_tunnels.pop(serial, None)
            if proc and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()

            # Remove adb forward
            await adb_forward_remove(conn.device.serial, conn.local_port)
            conn.active = False

        self._next_port = self._base_port
        logger.info("All tunnels removed")

    async def health_check(self, device: DeviceInfo) -> bool:
        """Verify tunnel is alive by attempting TCP connect on this server."""
        conn = self._connections.get(device.serial)
        if not conn or not conn.active:
            return False

        # Check SSH tunnel process is still alive
        proc = self._ssh_tunnels.get(device.serial)
        if proc and proc.returncode is not None:
            conn.active = False
            return False

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", conn.local_port),
                timeout=3,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def list_connections(self) -> list[TransportConnection]:
        return list(self._connections.values())

    def get_rpc_endpoints(self) -> list[str]:
        """Get RPC endpoints for llama-server --rpc flag."""
        return [conn.local_endpoint for conn in self._connections.values() if conn.active]

    def get_rpc_arg(self) -> str:
        """Get the --rpc argument string for llama-server."""
        return ",".join(self.get_rpc_endpoints())
