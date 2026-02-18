"""Prima.cpp inference orchestrator — pipeline-ring parallelism.

One-command flow:
1. Discover devices, verify prima-worker exists
2. Ensure model is on all phones (full GGUF needed per device for mmap)
3. Set up ring topology tunnels (ZMQ data + signal ports)
4. Start prima-worker on each phone with rank/layer assignments
5. Start prima-host (rank 0) on this server, serve API
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal

from loguru import logger
from rich.console import Console

from adb_llm.core.config import (
    DEFAULT_CONTEXT_SIZE,
    LLAMA_SERVER_HOST,
    LLAMA_SERVER_PORT,
    PRIMA_DATA_PORT,
    PRIMA_SIGNAL_PORT,
    REMOTE_MODELS_DIR,
    REMOTE_PRIMA_WORKER,
)
from adb_llm.core.device import DeviceInfo
from adb_llm.core.device_manager import DeviceManager
from adb_llm.core.errors import InferenceError
from adb_llm.inference.prima_manager import PrimaManager
from adb_llm.transport.prima_ring import PrimaRingTransport


class PrimaOrchestrator:
    """Full prima.cpp pipeline: devices -> ring tunnels -> workers -> host."""

    def __init__(self) -> None:
        self.device_manager = DeviceManager()
        self.ring_transport = PrimaRingTransport()
        self.prima_manager = PrimaManager()
        self.console = Console()
        self._host_process: asyncio.subprocess.Process | None = None

    async def start(
        self,
        model_path: str,
        port: int = LLAMA_SERVER_PORT,
        host: str = LLAMA_SERVER_HOST,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        target_devices: str = "all",
        total_layers: int = 64,
        prefetch: bool = True,
    ) -> None:
        """Full startup sequence for prima.cpp pipeline inference."""

        # 1. Discover and verify devices
        self.console.print("[bold]1/5[/] Discovering devices...")
        await self.device_manager.discover()
        await self.device_manager.probe_all()
        devices = self.device_manager.resolve_targets(target_devices)

        if not devices:
            raise InferenceError("No devices available")

        # Check prima-worker binary exists on devices
        ready: list[DeviceInfo] = []
        for dev in devices:
            from adb_llm.utils.adb import adb_shell
            out = await adb_shell(dev.serial, f"ls {REMOTE_PRIMA_WORKER}", check=False)
            if "No such file" not in out and out.strip():
                ready.append(dev)
            else:
                logger.warning("{} missing prima-worker binary", dev.serial[:8])

        if not ready:
            raise InferenceError(
                "No devices have prima-worker installed. Run:\n"
                "  ./scripts/build_prima.sh   (build the binary)\n"
                "  adb-llm devices setup      (push to phones)"
            )

        self.console.print(f"  Found {len(ready)} device(s) with prima-worker\n")

        # 2. Verify model exists on all phones
        self.console.print("[bold]2/5[/] Checking model on devices...")
        model_name = os.path.basename(model_path)
        remote_model = f"{REMOTE_MODELS_DIR}/{model_name}"
        devices_with_model: list[DeviceInfo] = []

        for dev in ready:
            from adb_llm.utils.adb import adb_shell
            out = await adb_shell(dev.serial, f"ls -la {remote_model}", check=False)
            if "No such file" not in out and out.strip():
                devices_with_model.append(dev)
            else:
                logger.warning("{} missing model {}", dev.serial[:8], model_name)

        if not devices_with_model:
            raise InferenceError(
                f"Model '{model_name}' not found on any device. Push it first:\n"
                f"  adb-llm distribute {model_path}"
            )

        self.console.print(
            f"  {len(devices_with_model)}/{len(ready)} devices have {model_name}\n"
        )

        # 3. Set up ring topology tunnels
        self.console.print("[bold]3/5[/] Setting up ring topology...")
        topology = await self.ring_transport.setup_ring(devices_with_model)
        layer_window = topology.layer_assignment(total_layers)

        self.console.print(f"  Ring: {topology.world_size} nodes (1 host + {len(devices_with_model)} phones)")
        self.console.print(f"  Layer assignment: {layer_window}")
        self.console.print()

        # 4. Start prima-worker on all phones
        self.console.print("[bold]4/5[/] Starting prima-workers...")
        results = await self.prima_manager.start_all(
            devices=devices_with_model,
            topology=topology,
            model_path=remote_model,
            layer_window=layer_window,
            context_size=context_size,
            prefetch=prefetch,
        )

        ok_count = sum(1 for v in results.values() if v)
        if ok_count == 0:
            raise InferenceError("Failed to start prima-worker on any device")
        if ok_count < len(devices_with_model):
            logger.warning(
                "Only {}/{} workers started — ring may not function",
                ok_count, len(devices_with_model),
            )
        self.console.print(f"  {ok_count}/{len(devices_with_model)} prima-workers started\n")

        # 5. Start prima-host (rank 0) on this server
        self.console.print("[bold]5/5[/] Starting prima-host (rank 0)...")
        await self._start_host(
            model_path=model_path,
            topology_size=topology.world_size,
            layer_window=layer_window,
            context_size=context_size,
            prefetch=prefetch,
        )

        self.console.print(
            f"\n[bold green]Ready![/] prima.cpp pipeline inference active."
        )
        self.console.print(f"  World size: {topology.world_size}")
        self.console.print(f"  Layers: {layer_window}")
        self.console.print(f"  Devices: {len(devices_with_model)} phones")
        self.console.print("\nPress Ctrl+C to stop.\n")

    async def _start_host(
        self,
        model_path: str,
        topology_size: int,
        layer_window: list[int],
        context_size: int = 2048,
        prefetch: bool = True,
    ) -> None:
        """Start prima-host (rank 0) on this Linux server."""
        prima_host_bin = shutil.which("prima-host")
        if not prima_host_bin:
            # Check in project bin dir
            project_bin = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))
                ))),
                "bin", "prima-host",
            )
            if os.path.isfile(project_bin) and os.access(project_bin, os.X_OK):
                prima_host_bin = project_bin
            else:
                raise InferenceError(
                    f"prima-host binary not found. Build it first:\n"
                    f"  ./scripts/build_prima.sh"
                )

        if self._host_process and self._host_process.returncode is None:
            logger.warning("prima-host already running, stopping first")
            await self._stop_host()

        lw_str = ",".join(str(n) for n in layer_window)

        # The first node in the ring after host is reachable via the topology
        # prima-host needs --next to point to rank 1's data port (reachable from host)
        topology = self.ring_transport.topology
        if topology and len(topology.nodes) > 1:
            next_node = topology.nodes[1]
            next_ip = "127.0.0.1"
        else:
            raise InferenceError("Ring topology not established")

        args = [
            prima_host_bin,
            "-m", model_path,
            "--world", str(topology_size),
            "--rank", "0",
            "--master", "127.0.0.1",
            "--next", next_ip,
            "--data-port", str(PRIMA_DATA_PORT),
            "--signal-port", str(PRIMA_SIGNAL_PORT),
            "-lw", lw_str,
            "-c", str(context_size),
            "-n", "-1",
        ]

        if prefetch:
            args.append("--prefetch")

        logger.info("Starting prima-host: {}", " ".join(args))

        self._host_process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait briefly to check it didn't crash
        try:
            await asyncio.wait_for(self._host_process.wait(), timeout=5)
            stderr = (await self._host_process.stderr.read()).decode(errors="replace")
            raise InferenceError(f"prima-host exited immediately:\n{stderr[:1000]}")
        except asyncio.TimeoutError:
            pass  # Still running — good

        logger.info("prima-host started (PID {})", self._host_process.pid)

    async def _stop_host(self) -> None:
        """Stop prima-host process."""
        if self._host_process and self._host_process.returncode is None:
            logger.info("Stopping prima-host (PID {})", self._host_process.pid)
            self._host_process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._host_process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._host_process.kill()
                await self._host_process.wait()
            self._host_process = None

    async def run(self, **kwargs) -> None:
        """Start and wait for shutdown signal."""
        await self.start(**kwargs)

        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()

        def _signal_handler():
            logger.info("Shutdown requested")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        # Stream host logs while waiting
        log_task = asyncio.create_task(self._stream_host_logs())

        await stop_event.wait()
        await self.shutdown()
        log_task.cancel()

    async def _stream_host_logs(self) -> None:
        """Stream prima-host stderr to loguru."""
        if not self._host_process or not self._host_process.stderr:
            return
        async for line in self._host_process.stderr:
            text = line.decode(errors="replace").rstrip()
            if text:
                logger.info("[prima-host] {}", text)

    async def shutdown(self) -> None:
        """Clean shutdown of everything."""
        self.console.print("\n[bold]Shutting down prima.cpp pipeline...[/]")

        # Stop host first
        await self._stop_host()

        # Stop all workers
        if self.ring_transport.topology:
            devices = [
                n.device for n in self.ring_transport.topology.nodes
                if n.device is not None
            ]
            await self.prima_manager.stop_all(devices)

        # Tear down tunnels
        await self.ring_transport.teardown_ring()

        self.console.print("[green]Prima.cpp shutdown complete.[/]")

    async def status(self) -> dict:
        """Get current ring status."""
        info: dict = {"active": False, "nodes": []}
        topology = self.ring_transport.topology
        if not topology:
            return info

        info["active"] = True
        info["world_size"] = topology.world_size

        for node in topology.nodes:
            node_info = {
                "rank": node.rank,
                "serial": node.serial or "HOST",
                "data_port": node.data_bind_port,
            }
            if node.device:
                node_info["running"] = await self.prima_manager.is_running(node.device)
            else:
                node_info["running"] = (
                    self._host_process is not None
                    and self._host_process.returncode is None
                )
            info["nodes"].append(node_info)

        return info
