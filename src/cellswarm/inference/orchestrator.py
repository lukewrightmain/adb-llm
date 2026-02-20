"""Inference orchestrator - ties everything together.

One-command flow:
1. Discover devices
2. Start swarm-rpc on each phone
3. Create USB tunnels
4. Launch swarm-server on host with RPC endpoints
5. Monitor health
"""

from __future__ import annotations

import asyncio
import signal

from loguru import logger
from rich.console import Console

from cellswarm.core.config import (
    DEFAULT_CONTEXT_SIZE,
    SWARM_SERVER_HOST,
    SWARM_SERVER_PORT,
    REMOTE_MODELS_DIR,
    RPC_BASE_LOCAL_PORT,
    RPC_REMOTE_PORT,
)
from cellswarm.core.device import DeviceInfo
from cellswarm.core.device_manager import DeviceManager
from cellswarm.core.errors import InferenceError
from cellswarm.inference.swarm_rpc_manager import SwarmRpcManager
from cellswarm.inference.swarm_server_manager import SwarmServerManager
from cellswarm.transport.adb_forward import AdbForwardTransport
from cellswarm.transport.health import HealthMonitor


class InferenceOrchestrator:
    """Full inference pipeline: devices -> tunnels -> swarm-server."""

    def __init__(self) -> None:
        self.device_manager = DeviceManager()
        self.transport = AdbForwardTransport(base_port=RPC_BASE_LOCAL_PORT)
        self.swarm_rpc_manager = SwarmRpcManager(port=RPC_REMOTE_PORT)
        self.swarm_server_manager = SwarmServerManager()
        self.health_monitor = HealthMonitor(self.transport)
        self.console = Console()

    async def start(
        self,
        model_path: str,
        port: int = SWARM_SERVER_PORT,
        host: str = SWARM_SERVER_HOST,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        tensor_split: str | None = None,
        target_devices: str = "all",
        extra_args: list[str] | None = None,
    ) -> None:
        """Full startup sequence."""

        # 1. Discover and probe devices
        self.console.print("[bold]1/4[/] Discovering devices...")
        await self.device_manager.discover()
        await self.device_manager.probe_all()
        devices = self.device_manager.resolve_targets(target_devices)

        if not devices:
            raise InferenceError("No devices available")

        ready = [d for d in devices if d.has_swarm_rpc]
        if not ready:
            raise InferenceError(
                "No devices have swarm-rpc installed. Run:\n"
                "  cellswarm devices probe   (to check)\n"
                "  # Push swarm-rpc binary to each device"
            )

        self.console.print(f"  Found {len(ready)} device(s) with swarm-rpc\n")

        # 2. Start swarm-rpc on all devices
        self.console.print("[bold]2/4[/] Starting swarm-rpc on devices...")
        results = await self.swarm_rpc_manager.start_all(ready)
        ok_devices = [d for d in ready if results.get(d.serial)]
        if not ok_devices:
            raise InferenceError("Failed to start swarm-rpc on any device")
        self.console.print(f"  {len(ok_devices)}/{len(ready)} swarm-rpcs started\n")

        # 3. Create USB tunnels
        self.console.print("[bold]3/4[/] Creating USB tunnels...")
        for dev in ok_devices:
            conn = await self.transport.connect(dev, RPC_REMOTE_PORT)
            self.console.print(f"  {dev.short_serial} -> {conn.local_endpoint}")
        self.console.print()

        # 4. Launch swarm-server
        self.console.print("[bold]4/4[/] Starting swarm-server...")
        rpc_arg = self.transport.get_rpc_arg()
        if not rpc_arg:
            raise InferenceError("No active tunnels for RPC")

        # Auto tensor-split: equal split across all devices
        if not tensor_split:
            n = len(ok_devices)
            tensor_split = ",".join([f"{1/n:.4f}"] * n)

        await self.swarm_server_manager.start(
            model_path=model_path,
            rpc_endpoints=rpc_arg,
            tensor_split=tensor_split,
            port=port,
            host=host,
            context_size=context_size,
            extra_args=extra_args,
        )

        self.console.print(f"\n[bold green]Ready![/] API at http://{host}:{port}/v1/chat/completions")
        self.console.print(f"  RPC endpoints: {rpc_arg}")
        self.console.print(f"  Tensor split: {tensor_split}")
        self.console.print(f"  Devices: {len(ok_devices)}")
        self.console.print("\nPress Ctrl+C to stop.\n")

    async def run(self, **kwargs) -> None:
        """Start and wait for shutdown signal."""
        await self.start(**kwargs)

        # Start health monitoring
        conns = await self.transport.list_connections()
        if conns:
            devices = [c.device for c in conns]
            self.health_monitor.start(devices, interval=5.0)

        # Wait for swarm-server to exit or Ctrl+C
        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()

        def _signal_handler():
            logger.info("Shutdown requested")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        # Stream logs while waiting
        log_task = asyncio.create_task(self.swarm_server_manager.stream_logs())

        await stop_event.wait()
        await self.shutdown()
        log_task.cancel()

    async def shutdown(self) -> None:
        """Clean shutdown of everything."""
        self.console.print("\n[bold]Shutting down...[/]")
        self.health_monitor.stop()
        await self.swarm_server_manager.stop()

        conns = await self.transport.list_connections()
        devices = [c.device for c in conns]

        await self.transport.disconnect_all()
        await self.swarm_rpc_manager.stop_all(devices)
        self.console.print("[green]Shutdown complete.[/]")
