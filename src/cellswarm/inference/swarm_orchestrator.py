"""CellSwarm inference orchestrator — pipeline-ring parallelism.

One-command flow:
1. Discover devices, verify cellswarm-worker exists
2. Ensure model is on all phones (full GGUF needed per device for mmap)
3. Set up ring topology tunnels (ZMQ data + signal ports)
4. Start cellswarm-worker on each phone with rank/layer assignments
5. Start cellswarm-host (rank 0) on this server, serve API
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal

from loguru import logger
from rich.console import Console

from cellswarm.core.config import (
    DEFAULT_CONTEXT_SIZE,
    SWARM_SERVER_HOST,
    SWARM_SERVER_PORT,
    SWARM_DATA_PORT,
    SWARM_SIGNAL_PORT,
    REMOTE_MODELS_DIR,
    REMOTE_SWARM_WORKER,
)

# Legacy paths from before adb-llm → cellswarm rename
_LEGACY_REMOTE_BASE = "/data/local/tmp/adb-llm"
_LEGACY_REMOTE_WORKER = f"{_LEGACY_REMOTE_BASE}/bin/prima-worker"
_LEGACY_REMOTE_MODELS_DIR = f"{_LEGACY_REMOTE_BASE}/models"
from cellswarm.core.device import DeviceInfo
from cellswarm.core.device_manager import DeviceManager
from cellswarm.core.errors import InferenceError
from cellswarm.inference.swarm_manager import SwarmManager
from cellswarm.transport.swarm_ring import SwarmRingTransport
from cellswarm.transport.ethernet_ring import EthernetRingTransport


def _is_ethernet(device: DeviceInfo) -> bool:
    """Check if a device is ethernet-connected (IP:port serial)."""
    return ":" in device.serial


class SwarmOrchestrator:
    """Full cellswarm pipeline: devices -> ring tunnels -> workers -> host."""

    def __init__(self) -> None:
        self.device_manager = DeviceManager()
        self.ring_transport: SwarmRingTransport | EthernetRingTransport = SwarmRingTransport()
        self.swarm_manager = SwarmManager()
        self.console = Console()
        self._host_process: asyncio.subprocess.Process | None = None
        self._use_ethernet = False

    async def start(
        self,
        model_path: str,
        port: int = SWARM_SERVER_PORT,
        host: str = SWARM_SERVER_HOST,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        target_devices: str = "all",
        total_layers: int = 64,
        prefetch: bool = True,
    ) -> None:
        """Full startup sequence for cellswarm pipeline inference."""

        # 1. Discover and verify devices
        self.console.print("[bold]1/5[/] Discovering devices...")
        await self.device_manager.discover()
        await self.device_manager.probe_all()
        devices = self.device_manager.resolve_targets(target_devices)

        if not devices:
            raise InferenceError("No devices available")

        # Check worker binary exists on devices (new or legacy path)
        ready: list[DeviceInfo] = []
        worker_paths: dict[str, str] = {}  # serial -> actual worker path
        for dev in devices:
            from cellswarm.utils.adb import adb_shell
            # Check new path first
            out = await adb_shell(dev.serial, f"ls {REMOTE_SWARM_WORKER}", check=False)
            if "No such file" not in out and out.strip():
                ready.append(dev)
                worker_paths[dev.serial] = REMOTE_SWARM_WORKER
            else:
                # Check legacy path (prima-worker)
                out2 = await adb_shell(dev.serial, f"ls {_LEGACY_REMOTE_WORKER}", check=False)
                if "No such file" not in out2 and out2.strip():
                    ready.append(dev)
                    worker_paths[dev.serial] = _LEGACY_REMOTE_WORKER
                    logger.info("{} using legacy prima-worker binary", dev.serial[:8])
                else:
                    logger.warning("{} missing worker binary", dev.serial[:8])

        if not ready:
            raise InferenceError(
                "No devices have a worker binary installed.\n"
                "Looking for:\n"
                f"  {REMOTE_SWARM_WORKER}\n"
                f"  {_LEGACY_REMOTE_WORKER}\n"
                "Build with: ./scripts/build_prima.sh"
            )

        self.console.print(f"  Found {len(ready)} device(s) with worker binary\n")
        # Store for later use by SwarmManager
        self._worker_paths = worker_paths

        # 2. Verify model exists on all phones (check new + legacy paths)
        self.console.print("[bold]2/5[/] Checking model on devices...")
        model_name = os.path.basename(model_path)
        remote_model_new = f"{REMOTE_MODELS_DIR}/{model_name}"
        remote_model_legacy = f"{_LEGACY_REMOTE_MODELS_DIR}/{model_name}"
        devices_with_model: list[DeviceInfo] = []
        model_paths: dict[str, str] = {}  # serial -> actual model path on device

        for dev in ready:
            from cellswarm.utils.adb import adb_shell
            # Check new path first
            out = await adb_shell(dev.serial, f"ls -la {remote_model_new}", check=False)
            if "No such file" not in out and out.strip():
                devices_with_model.append(dev)
                model_paths[dev.serial] = remote_model_new
            else:
                # Check legacy path
                out2 = await adb_shell(dev.serial, f"ls -la {remote_model_legacy}", check=False)
                if "No such file" not in out2 and out2.strip():
                    devices_with_model.append(dev)
                    model_paths[dev.serial] = remote_model_legacy
                    logger.info("{} using legacy model path", dev.serial[:8])
                else:
                    logger.warning("{} missing model {}", dev.serial[:8], model_name)

        if not devices_with_model:
            raise InferenceError(
                f"Model '{model_name}' not found on any device.\n"
                f"Checked:\n"
                f"  {remote_model_new}\n"
                f"  {remote_model_legacy}\n"
                f"Push it via the Models tab or run:\n"
                f"  cellswarm distribute {model_path}"
            )

        self.console.print(
            f"  {len(devices_with_model)}/{len(ready)} devices have {model_name}\n"
        )
        # Store for later
        self._model_paths = model_paths

        # 3. Set up ring topology
        # Auto-detect: if all devices are ethernet (IP:port), use direct IP transport
        self._use_ethernet = all(_is_ethernet(d) for d in devices_with_model)
        if self._use_ethernet:
            self.console.print("[bold]3/5[/] Setting up ethernet ring (direct IP)...")
            self.ring_transport = EthernetRingTransport()
        else:
            self.console.print("[bold]3/5[/] Setting up ring topology (tunnels)...")
            self.ring_transport = SwarmRingTransport()
        topology = await self.ring_transport.setup_ring(devices_with_model)
        layer_window = topology.layer_assignment(total_layers)

        self.console.print(f"  Ring: {topology.world_size} nodes (1 host + {len(devices_with_model)} phones)")
        self.console.print(f"  Layer assignment: {layer_window}")
        self.console.print()

        # 4. Start workers on all phones (using per-device binary + model paths)
        self.console.print("[bold]4/5[/] Starting workers...")
        results = await self.swarm_manager.start_all(
            devices=devices_with_model,
            topology=topology,
            model_path=remote_model_new,  # default, overridden per-device below
            layer_window=layer_window,
            context_size=context_size,
            prefetch=prefetch,
            worker_paths=self._worker_paths,
            model_paths=self._model_paths,
        )

        ok_count = sum(1 for v in results.values() if v)
        if ok_count == 0:
            raise InferenceError("Failed to start cellswarm-worker on any device")
        if ok_count < len(devices_with_model):
            logger.warning(
                "Only {}/{} workers started — ring may not function",
                ok_count, len(devices_with_model),
            )
        self.console.print(f"  {ok_count}/{len(devices_with_model)} cellswarm-workers started\n")

        # 5. Start cellswarm-host (rank 0) on this server
        self.console.print("[bold]5/5[/] Starting cellswarm-host (rank 0)...")
        await self._start_host(
            model_path=model_path,
            topology_size=topology.world_size,
            layer_window=layer_window,
            context_size=context_size,
            prefetch=prefetch,
        )

        self.console.print(
            f"\n[bold green]Ready![/] cellswarm pipeline inference active."
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
        """Start cellswarm-host (rank 0) on this Linux server."""
        swarm_host_bin = shutil.which("cellswarm-host") or shutil.which("prima-host")
        if not swarm_host_bin:
            # Check in project bin dir (try both new and legacy names)
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )))
            for name in ("cellswarm-host", "prima-host", "prima-host-spec"):
                candidate = os.path.join(project_root, "bin", name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    swarm_host_bin = candidate
                    break
            if not swarm_host_bin:
                raise InferenceError(
                    "Host binary not found. Looking for cellswarm-host or prima-host.\n"
                    "Build with: ./scripts/build_prima.sh"
                )

        if self._host_process and self._host_process.returncode is None:
            logger.warning("cellswarm-host already running, stopping first")
            await self._stop_host()

        lw_str = ",".join(str(n) for n in layer_window)

        # The first node in the ring after host is reachable via the topology
        # cellswarm-host needs --next to point to rank 1's data port (reachable from host)
        topology = self.ring_transport.topology
        if topology and len(topology.nodes) > 1:
            next_node = topology.nodes[1]
            # For ethernet: use direct phone IP; for USB: use localhost (tunneled)
            if hasattr(next_node, 'ip') and next_node.ip:
                next_ip = next_node.ip
            else:
                next_ip = "127.0.0.1"
        else:
            raise InferenceError("Ring topology not established")

        # Direct/ethernet: host binds 0.0.0.0 and advertises its real IP
        # SSH mode: host binds on 127.0.0.1 (tunnels handle routing)
        host_node = topology.nodes[0]
        if (self._use_ethernet or getattr(topology, 'mode', '') == 'direct') \
                and hasattr(host_node, 'ip') and host_node.ip and host_node.ip != "127.0.0.1":
            master_ip = host_node.ip
        else:
            master_ip = "127.0.0.1"

        args = [
            swarm_host_bin,
            "-m", model_path,
            "--world", str(topology_size),
            "--rank", "0",
            "--master", master_ip,
            "--next", next_ip,
            "--data-port", str(SWARM_DATA_PORT),
            "--signal-port", str(SWARM_SIGNAL_PORT),
            "-lw", lw_str,
            "-c", str(context_size),
            "-n", "-1",
        ]

        if prefetch:
            args.append("--prefetch")

        logger.info("Starting cellswarm-host: {}", " ".join(args))

        self._host_process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait briefly to check it didn't crash
        try:
            await asyncio.wait_for(self._host_process.wait(), timeout=5)
            stderr = (await self._host_process.stderr.read()).decode(errors="replace")
            raise InferenceError(f"cellswarm-host exited immediately:\n{stderr[:1000]}")
        except asyncio.TimeoutError:
            pass  # Still running — good

        logger.info("cellswarm-host started (PID {})", self._host_process.pid)

    async def _stop_host(self) -> None:
        """Stop cellswarm-host process."""
        if self._host_process and self._host_process.returncode is None:
            logger.info("Stopping cellswarm-host (PID {})", self._host_process.pid)
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
        """Stream cellswarm-host stderr to loguru."""
        if not self._host_process or not self._host_process.stderr:
            return
        async for line in self._host_process.stderr:
            text = line.decode(errors="replace").rstrip()
            if text:
                logger.info("[cellswarm-host] {}", text)

    async def shutdown(self) -> None:
        """Clean shutdown of everything."""
        self.console.print("\n[bold]Shutting down cellswarm pipeline...[/]")

        # Stop host first
        await self._stop_host()

        # Stop all workers
        if self.ring_transport.topology:
            devices = [
                n.device for n in self.ring_transport.topology.nodes
                if n.device is not None
            ]
            await self.swarm_manager.stop_all(devices)

        # Tear down tunnels
        await self.ring_transport.teardown_ring()

        self.console.print("[green]CellSwarm shutdown complete.[/]")

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
                node_info["running"] = await self.swarm_manager.is_running(node.device)
            else:
                node_info["running"] = (
                    self._host_process is not None
                    and self._host_process.returncode is None
                )
            info["nodes"].append(node_info)

        return info
