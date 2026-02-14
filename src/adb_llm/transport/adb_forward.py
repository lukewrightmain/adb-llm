"""TCP tunneling transport via `adb forward`."""

from __future__ import annotations

import asyncio

from loguru import logger

from adb_llm.core.config import RPC_BASE_LOCAL_PORT, RPC_REMOTE_PORT
from adb_llm.core.device import DeviceInfo
from adb_llm.core.errors import TunnelError
from adb_llm.transport.base import TransportConnection, TransportLayer
from adb_llm.utils.adb import adb_forward, adb_forward_list, adb_forward_remove


class AdbForwardTransport(TransportLayer):
    """Transport via `adb forward` TCP port forwarding over USB.

    Creates one localhost port per device, forwarding to the device's
    rpc-server port. This gives llama-server on the host PC a set of
    localhost:600XX endpoints to connect to.
    """

    def __init__(self, base_port: int = RPC_BASE_LOCAL_PORT) -> None:
        self._base_port = base_port
        self._connections: dict[str, TransportConnection] = {}  # serial -> conn
        self._next_port = base_port

    async def connect(
        self, device: DeviceInfo, remote_port: int = RPC_REMOTE_PORT
    ) -> TransportConnection:
        if device.serial in self._connections:
            return self._connections[device.serial]

        local_port = self._next_port
        self._next_port += 1

        try:
            await adb_forward(device.serial, local_port, remote_port)
        except Exception as e:
            raise TunnelError(device.serial, str(e)) from e

        conn = TransportConnection(
            device=device,
            local_port=local_port,
            remote_port=remote_port,
        )
        self._connections[device.serial] = conn
        logger.info("Tunnel: {} -> localhost:{}", device.serial, local_port)
        return conn

    async def disconnect(self, device: DeviceInfo) -> None:
        conn = self._connections.pop(device.serial, None)
        if conn:
            await adb_forward_remove(device.serial, conn.local_port)
            conn.active = False
            logger.info("Removed tunnel for {}", device.serial)

    async def disconnect_all(self) -> None:
        for serial in list(self._connections):
            conn = self._connections.pop(serial)
            await adb_forward_remove(conn.device.serial, conn.local_port)
            conn.active = False
        self._next_port = self._base_port
        logger.info("All tunnels removed")

    async def health_check(self, device: DeviceInfo) -> bool:
        """Verify tunnel is alive by attempting TCP connect."""
        conn = self._connections.get(device.serial)
        if not conn or not conn.active:
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
        """Get comma-separated RPC endpoints for llama-server --rpc flag."""
        return [conn.local_endpoint for conn in self._connections.values() if conn.active]

    def get_rpc_arg(self) -> str:
        """Get the --rpc argument string for llama-server."""
        return ",".join(self.get_rpc_endpoints())
