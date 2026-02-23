"""Manage cellswarm-worker lifecycle on Android devices."""

from __future__ import annotations

import asyncio

from loguru import logger

from cellswarm.core.config import SWARM_START_TIMEOUT, REMOTE_SWARM_WORKER
from cellswarm.core.device import DeviceInfo
from cellswarm.core.errors import RpcServerError
from cellswarm.transport.swarm_ring import RingNode, RingTopology
from cellswarm.transport.ethernet_ring import EthernetRingNode, EthernetRingTopology
from cellswarm.transport.usb_tether import TetherNode, TetherTopology
from cellswarm.utils.adb import adb_shell, adb_shell_bg


class SwarmManager:
    """Start/stop/monitor cellswarm-worker on Android devices."""

    async def start(
        self,
        device: DeviceInfo,
        node: RingNode | EthernetRingNode,
        topology: RingTopology | EthernetRingTopology,
        model_path: str,
        layer_window: list[int],
        context_size: int = 2048,
        prefetch: bool = True,
        swarm_bin: str | None = None,
        act_quant: str = "fp32",
    ) -> None:
        """Start cellswarm-worker on a phone with ring topology parameters.

        Args:
            device: Target phone.
            node: This phone's RingNode (rank, ports, etc.).
            topology: Full ring topology for address resolution.
            model_path: Remote path to GGUF model on phone.
            layer_window: Layer counts per rank (comma-separated for -lw flag).
            context_size: Context window size.
            prefetch: Enable weight prefetching.
            swarm_bin: Override cellswarm-worker binary path.
            act_quant: Activation quantization ("fp32" or "fp16").
        """
        bin_path = swarm_bin or REMOTE_SWARM_WORKER

        # Kill any existing instance
        await self.stop(device)

        # Build worker command
        # RingNode has phone_next_ip/phone_master_ip (direct=real IP, ssh=127.0.0.1)
        # EthernetRingNode has next_ip/master_ip
        if hasattr(node, 'phone_next_ip') and node.phone_next_ip:
            next_addr = node.phone_next_ip
            master_addr = node.phone_master_ip
        elif hasattr(node, 'next_ip') and node.next_ip:
            next_addr = node.next_ip
            master_addr = node.master_ip
        else:
            next_addr = "127.0.0.1"
            master_addr = "127.0.0.1"

        lw_str = ",".join(str(n) for n in layer_window)

        # Pin to big cores (Snapdragon 888: cores 4-7 = 1x X1 + 3x A78)
        # taskset 0xF0 = bitmask for cores 4,5,6,7
        # Use 4 threads to match the 4 big cores
        parts = [
            f"cd /data/local/tmp &&",
            f"taskset f0 {bin_path}",
            f"-m {model_path}",
            f"--world {topology.world_size}",
            f"--rank {node.rank}",
            f"--master {master_addr}",
            f"--next {next_addr}",
            f"--data-port {node.data_bind_port}",
            f"--signal-port {node.signal_bind_port}",
            f"-lw {lw_str}",
            f"-c {context_size}",
            f"-t 4",
            f"-n -1",
            "--no-mmap",
        ]

        if prefetch:
            parts.append("--prefetch")

        if act_quant == "fp16":
            parts.append("--act-quant fp16")

        cmd = " ".join(parts)
        # Redirect stdout/stderr to log; adb_shell_bg wraps in sh -c '... &'
        # and closes host-side stdin so ADB returns immediately
        full_cmd = f"{cmd} > /data/local/tmp/swarm-worker.log 2>&1"
        logger.debug("Starting cellswarm-worker on {}: {}", device.serial[:8], full_cmd)
        await adb_shell_bg(device.serial, full_cmd)

        # Wait for it to come up
        for _ in range(SWARM_START_TIMEOUT):
            if await self.is_running(device):
                logger.info(
                    "cellswarm-worker started on {} (rank {}, port {})",
                    device.serial[:8], node.rank, node.data_bind_port,
                )
                return
            await asyncio.sleep(1)

        # Grab logs for debugging
        log_output = await adb_shell(
            device.serial, "cat /data/local/tmp/swarm-worker.log", check=False,
        )
        raise RpcServerError(
            device.serial,
            f"cellswarm-worker failed to start within {SWARM_START_TIMEOUT}s. "
            f"Log:\n{log_output[:500]}",
        )

    async def start_tethered(
        self,
        device: DeviceInfo,
        node: TetherNode,
        topology: TetherTopology,
        model_path: str,
        layer_window: list[int],
        context_size: int = 2048,
        prefetch: bool = True,
        swarm_bin: str | None = None,
        act_quant: str = "fp32",
    ) -> None:
        """Start cellswarm-worker on a phone with USB tethering transport.

        Instead of localhost tunnels, workers connect directly to
        other phones' tethering IPs.
        """
        bin_path = swarm_bin or REMOTE_SWARM_WORKER

        await self.stop(device)

        lw_str = ",".join(str(n) for n in layer_window)

        parts = [
            f"cd /data/local/tmp &&",
            f"taskset f0 {bin_path}",
            f"-m {model_path}",
            f"--world {topology.world_size}",
            f"--rank {node.rank}",
            f"--master {node.master_ip}",
            f"--next {node.next_ip}",
            f"--data-port {node.data_bind_port}",
            f"--signal-port {node.signal_bind_port}",
            f"-lw {lw_str}",
            f"-c {context_size}",
            f"-t 4",
            f"-n -1",
            "--no-mmap",
        ]

        if prefetch:
            parts.append("--prefetch")

        if act_quant == "fp16":
            parts.append("--act-quant fp16")

        cmd = " ".join(parts)
        full_cmd = f"{cmd} > /data/local/tmp/swarm-worker.log 2>&1"
        logger.debug("Starting tethered cellswarm-worker on {}: {}", device.serial[:8], full_cmd)
        await adb_shell_bg(device.serial, full_cmd)

        for _ in range(SWARM_START_TIMEOUT):
            if await self.is_running(device):
                logger.info(
                    "cellswarm-worker (tethered) started on {} (rank {}, ip={})",
                    device.serial[:8], node.rank, node.tether_ip,
                )
                return
            await asyncio.sleep(1)

        log_output = await adb_shell(
            device.serial, "cat /data/local/tmp/swarm-worker.log", check=False,
        )
        raise RpcServerError(
            device.serial,
            f"cellswarm-worker (tethered) failed to start within {SWARM_START_TIMEOUT}s. "
            f"Log:\n{log_output[:500]}",
        )

    async def stop(self, device: DeviceInfo) -> None:
        """Kill worker on a device (handles both new and legacy binary names)."""
        await adb_shell(device.serial, "pkill -f cellswarm-worker; pkill -f prima-worker", check=False)
        await asyncio.sleep(0.5)
        logger.debug("worker stopped on {}", device.serial[:8])

    async def is_running(self, device: DeviceInfo) -> bool:
        """Check if worker is running on a device (handles both binary names)."""
        out = await adb_shell(device.serial, "pidof cellswarm-worker || pidof prima-worker", check=False)
        return bool(out.strip())

    async def start_all(
        self,
        devices: list[DeviceInfo],
        topology: RingTopology | EthernetRingTopology,
        model_path: str,
        layer_window: list[int],
        context_size: int = 2048,
        prefetch: bool = True,
        act_quant: str = "fp32",
        worker_paths: dict[str, str] | None = None,
        model_paths: dict[str, str] | None = None,
    ) -> dict[str, bool]:
        """Start worker on all phones. Returns {serial: success}.

        Args:
            worker_paths: Optional per-device worker binary paths (serial -> path).
            model_paths: Optional per-device model paths (serial -> path).
        """

        async def _start_one(dev: DeviceInfo, rank: int) -> tuple[str, bool]:
            node = topology.get_node(rank)
            dev_worker = (worker_paths or {}).get(dev.serial)
            dev_model = (model_paths or {}).get(dev.serial, model_path)
            try:
                await self.start(
                    device=dev,
                    node=node,
                    topology=topology,
                    model_path=dev_model,
                    layer_window=layer_window,
                    context_size=context_size,
                    prefetch=prefetch,
                    swarm_bin=dev_worker,
                    act_quant=act_quant,
                )
                return dev.serial, True
            except Exception as e:
                logger.error("Failed to start worker on {}: {}", dev.serial[:8], e)
                return dev.serial, False

        # Start phones sequentially (rank order matters for ZMQ binding)
        results: dict[str, bool] = {}
        for idx, dev in enumerate(devices):
            serial, ok = await _start_one(dev, idx + 1)
            results[serial] = ok
        return results

    async def start_all_tethered(
        self,
        devices: list[DeviceInfo],
        topology: TetherTopology,
        model_path: str,
        layer_window: list[int],
        context_size: int = 2048,
        prefetch: bool = True,
        act_quant: str = "fp32",
    ) -> dict[str, bool]:
        """Start cellswarm-worker on all phones with tethering transport."""

        async def _start_one(dev: DeviceInfo, rank: int) -> tuple[str, bool]:
            node = topology.get_node(rank)
            try:
                await self.start_tethered(
                    device=dev,
                    node=node,
                    topology=topology,
                    model_path=model_path,
                    layer_window=layer_window,
                    context_size=context_size,
                    prefetch=prefetch,
                    act_quant=act_quant,
                )
                return dev.serial, True
            except Exception as e:
                logger.error("Failed to start tethered cellswarm-worker on {}: {}", dev.serial[:8], e)
                return dev.serial, False

        results: dict[str, bool] = {}
        for idx, dev in enumerate(devices):
            serial, ok = await _start_one(dev, idx)
            results[serial] = ok
        return results

    async def stop_all(self, devices: list[DeviceInfo]) -> None:
        """Stop cellswarm-worker on all devices."""
        await asyncio.gather(*[self.stop(d) for d in devices])

    async def get_logs(self, device: DeviceInfo, lines: int = 50) -> str:
        """Get recent cellswarm-worker logs from a device."""
        out = await adb_shell(
            device.serial,
            f"tail -n {lines} /data/local/tmp/swarm-worker.log",
            check=False,
        )
        return out
