"""Ring topology transport for cellswarm pipeline-ring parallelism.

cellswarm uses ZMQ PUSH/PULL sockets in a ring:
  Host(rank0) -> Phone1(rank1) -> Phone2(rank2) -> ... -> Host(rank0)

Supports two modes:

**direct** — Phones are IP-reachable.  No tunnels needed.
  Host connects to PhoneIP:port directly. Phones connect to each
  other (and to the host) via direct IP.

**ssh** (legacy) — Phones behind a Windows PC.
  Uses adb forward/reverse + SSH tunnels through WinPC.
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field

from loguru import logger

from cellswarm.core.config import (
    SWARM_DATA_PORT,
    SWARM_FORWARD_BASE,
    SWARM_SIGNAL_PORT,
    SWARM_SSH_REVERSE_BASE,
    load_config,
)
from cellswarm.core.device import DeviceInfo
from cellswarm.core.errors import TunnelError
from cellswarm.utils.adb import (
    adb_forward,
    adb_reverse,
    ssh_reverse_tunnel,
    ssh_tunnel,
)


@dataclass
class RingNode:
    """One node in the ring topology."""
    rank: int
    device: DeviceInfo | None  # None for host (rank 0)
    serial: str  # "" for host
    data_bind_port: int        # Port this node binds PULL on (data_port + rank)
    signal_bind_port: int      # Port this node binds PULL on (signal_port + rank)
    # How to reach this node's bind ports from the host (localhost:X or IP:X)
    host_reachable_data_port: int
    host_reachable_signal_port: int
    # Direct IP of this node (used in direct mode; "127.0.0.1" for SSH mode)
    ip: str = "127.0.0.1"
    # WinPC-side ADB forward ports (SSH mode only)
    winpc_fwd_data_port: int = 0
    winpc_fwd_signal_port: int = 0
    # What the phone sees as its next node address (localhost:X on phone in SSH mode,
    # or next_phone_IP:X in direct mode)
    phone_next_data_port: int = 0    # Port phone connects to for PUSH to successor
    phone_next_signal_port: int = 0
    # IP the phone connects to for its next node
    phone_next_ip: str = "127.0.0.1"
    # IP the phone uses for --master
    phone_master_ip: str = "127.0.0.1"


@dataclass
class RingTopology:
    """Complete ring topology with tunnel metadata."""
    nodes: list[RingNode] = field(default_factory=list)
    world_size: int = 0
    mode: str = "direct"  # "direct" or "ssh"

    def get_node(self, rank: int) -> RingNode:
        return self.nodes[rank]

    def layer_assignment(self, total_layers: int) -> list[int]:
        """Divide layers across nodes. Host gets 1 layer (minimum), phones split the rest.

        cellswarm requires all layer window values > 0.
        """
        n_phones = self.world_size - 1
        if n_phones <= 0:
            return [total_layers]
        host_layers = 1
        phone_total = total_layers - host_layers
        base = phone_total // n_phones
        remainder = phone_total % n_phones
        layers = [host_layers]
        for i in range(n_phones):
            extra = 1 if i < remainder else 0
            layers.append(base + extra)
        return layers


def _get_host_ip() -> str:
    """Determine the host's IP reachable by phones.

    Uses config.host_ip if set, otherwise auto-detects.
    """
    cfg = load_config()
    if cfg.host_ip:
        return cfg.host_ip
    # Auto-detect: create a UDP socket to a known IP (doesn't actually send)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.105.0.1", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _device_ip(device: DeviceInfo) -> str:
    """Extract the IP from a TCP/IP device serial like '10.105.0.12:5555'."""
    if ":" in device.serial:
        return device.serial.split(":")[0]
    return ""


class SwarmRingTransport:
    """Manage the full ring of tunnels for cellswarm pipeline parallelism.

    The ring flows: Host(0) -> Phone1(1) -> Phone2(2) -> ... -> Host(0)

    In **direct mode** (phones IP-reachable), no tunnels are needed.
    In **ssh mode** (legacy WinPC), full tunnel chains are created.
    """

    SIGNAL_OFFSET = 100  # Gap between data and signal port ranges

    def __init__(self) -> None:
        self._topology: RingTopology | None = None
        self._ssh_forward_procs: list[asyncio.subprocess.Process] = []
        self._ssh_reverse_procs: list[asyncio.subprocess.Process] = []

    async def setup_ring(self, devices: list[DeviceInfo]) -> RingTopology:
        """Build the ring topology — dispatches based on config mode."""
        cfg = load_config()
        if cfg.mode == "direct":
            return await self._setup_ring_direct(devices)
        else:
            return await self._setup_ring_ssh(devices)

    # ------------------------------------------------------------------
    # Direct mode — phones are IP-reachable, no tunnels
    # ------------------------------------------------------------------

    async def _setup_ring_direct(self, devices: list[DeviceInfo]) -> RingTopology:
        """Build a direct-IP ring.  No tunnels needed.

        Phones connect to each other and to the host via real IP addresses.
        """
        world_size = len(devices) + 1  # +1 for host at rank 0
        host_ip = _get_host_ip()
        topology = RingTopology(world_size=world_size, mode="direct")

        # Host node (rank 0)
        host_node = RingNode(
            rank=0,
            device=None,
            serial="",
            ip=host_ip,
            data_bind_port=SWARM_DATA_PORT,
            signal_bind_port=SWARM_SIGNAL_PORT,
            host_reachable_data_port=SWARM_DATA_PORT,
            host_reachable_signal_port=SWARM_SIGNAL_PORT,
        )
        topology.nodes.append(host_node)

        # Phone nodes (ranks 1..N)
        for idx, device in enumerate(devices):
            rank = idx + 1
            phone_ip = _device_ip(device)
            if not phone_ip:
                raise TunnelError(
                    device.serial,
                    f"Cannot determine IP for device {device.serial}. "
                    "Direct mode requires TCP/IP devices (IP:PORT serial).",
                )
            node = RingNode(
                rank=rank,
                device=device,
                serial=device.serial,
                ip=phone_ip,
                data_bind_port=SWARM_DATA_PORT + rank,
                signal_bind_port=SWARM_SIGNAL_PORT + rank,
                # Host reaches phone via its IP directly
                host_reachable_data_port=SWARM_DATA_PORT + rank,
                host_reachable_signal_port=SWARM_SIGNAL_PORT + rank,
            )
            topology.nodes.append(node)

        # Set next/master IPs for each phone
        for idx, device in enumerate(devices):
            rank = idx + 1
            next_rank = (rank + 1) % world_size
            next_node = topology.nodes[next_rank]
            topology.nodes[rank].phone_next_ip = next_node.ip
            topology.nodes[rank].phone_next_data_port = SWARM_DATA_PORT + next_rank
            topology.nodes[rank].phone_next_signal_port = SWARM_SIGNAL_PORT + next_rank
            topology.nodes[rank].phone_master_ip = host_ip

        self._topology = topology

        logger.info("Direct-IP ring topology established: {} nodes", world_size)
        for node in topology.nodes:
            label = "HOST" if node.rank == 0 else node.serial[:12]
            logger.info(
                "  rank {} ({}): ip={}, data_bind={}",
                node.rank, label, node.ip, node.data_bind_port,
            )

        return topology

    # ------------------------------------------------------------------
    # SSH mode — legacy WinPC tunnel chains
    # ------------------------------------------------------------------

    async def _setup_ring_ssh(self, devices: list[DeviceInfo]) -> RingTopology:
        """Build SSH-tunneled ring (legacy WinPC mode)."""
        world_size = len(devices) + 1
        topology = RingTopology(world_size=world_size, mode="ssh")

        # Host node (rank 0) — binds directly on localhost
        host_node = RingNode(
            rank=0,
            device=None,
            serial="",
            data_bind_port=SWARM_DATA_PORT,
            signal_bind_port=SWARM_SIGNAL_PORT,
            host_reachable_data_port=SWARM_DATA_PORT,
            host_reachable_signal_port=SWARM_SIGNAL_PORT,
        )
        topology.nodes.append(host_node)

        # Phone nodes (ranks 1..N)
        for idx, device in enumerate(devices):
            rank = idx + 1
            node = RingNode(
                rank=rank,
                device=device,
                serial=device.serial,
                data_bind_port=SWARM_DATA_PORT + rank,
                signal_bind_port=SWARM_SIGNAL_PORT + rank,
                host_reachable_data_port=SWARM_DATA_PORT + rank,
                host_reachable_signal_port=SWARM_SIGNAL_PORT + rank,
                winpc_fwd_data_port=SWARM_FORWARD_BASE + idx,
                winpc_fwd_signal_port=SWARM_FORWARD_BASE + self.SIGNAL_OFFSET + idx,
            )
            topology.nodes.append(node)

        # Create inbound tunnels (so host/prev can reach each phone's bind ports)
        for idx, device in enumerate(devices):
            rank = idx + 1
            node = topology.nodes[rank]
            try:
                await self._create_inbound_tunnels(device, idx, node)
            except Exception as e:
                raise TunnelError(device.serial, f"Inbound tunnel failed: {e}") from e

        # Create outbound tunnels (so each phone can send to its successor)
        for idx, device in enumerate(devices):
            rank = idx + 1
            next_rank = (rank + 1) % world_size
            next_node = topology.nodes[next_rank]

            phone_next_data = SWARM_DATA_PORT + next_rank
            phone_next_signal = SWARM_SIGNAL_PORT + next_rank
            topology.nodes[rank].phone_next_data_port = phone_next_data
            topology.nodes[rank].phone_next_signal_port = phone_next_signal

            is_next_phone = next_node.serial != ""

            try:
                if is_next_phone:
                    await self._create_direct_outbound(
                        device, idx,
                        phone_next_data, next_node.winpc_fwd_data_port,
                        phone_next_signal, next_node.winpc_fwd_signal_port,
                    )
                else:
                    await self._create_outbound_tunnels(
                        device, idx,
                        phone_next_data, next_node.host_reachable_data_port,
                        phone_next_signal, next_node.host_reachable_signal_port,
                    )
            except Exception as e:
                raise TunnelError(device.serial, f"Outbound tunnel failed: {e}") from e

        self._topology = topology

        logger.info("SSH ring topology established: {} nodes", world_size)
        for node in topology.nodes:
            label = "HOST" if node.rank == 0 else node.serial[:8]
            logger.info(
                "  rank {} ({}): data_bind={}, reachable=localhost:{}",
                node.rank, label, node.data_bind_port, node.host_reachable_data_port,
            )

        return topology

    # ------------------------------------------------------------------
    # SSH mode tunnel helpers
    # ------------------------------------------------------------------

    async def _create_inbound_tunnels(
        self, device: DeviceInfo, idx: int, node: RingNode,
    ) -> None:
        """Create tunnels so that host can reach phone's ZMQ bind ports."""
        win_fwd_port = SWARM_FORWARD_BASE + idx
        await adb_forward(device.serial, win_fwd_port, node.data_bind_port)
        proc = await ssh_tunnel(node.host_reachable_data_port, win_fwd_port)
        if proc:
            self._ssh_forward_procs.append(proc)

        win_fwd_sig = SWARM_FORWARD_BASE + self.SIGNAL_OFFSET + idx
        await adb_forward(device.serial, win_fwd_sig, node.signal_bind_port)
        proc = await ssh_tunnel(node.host_reachable_signal_port, win_fwd_sig)
        if proc:
            self._ssh_forward_procs.append(proc)

        logger.debug(
            "Inbound tunnels for {} (rank {}): data localhost:{}, signal localhost:{}",
            device.serial[:8], node.rank,
            node.host_reachable_data_port, node.host_reachable_signal_port,
        )

    async def _create_direct_outbound(
        self,
        device: DeviceInfo,
        idx: int,
        phone_data_port: int,
        winpc_fwd_data_port: int,
        phone_signal_port: int,
        winpc_fwd_signal_port: int,
    ) -> None:
        """Create DIRECT phone-to-phone tunnels via WinPC (no SSH)."""
        await adb_reverse(device.serial, phone_data_port, winpc_fwd_data_port)
        await adb_reverse(device.serial, phone_signal_port, winpc_fwd_signal_port)

        logger.info(
            "DIRECT outbound for {} (rank {}): phone:{} -> winpc:{} -> next phone",
            device.serial[:8], idx + 1,
            phone_data_port, winpc_fwd_data_port,
        )

    async def _create_outbound_tunnels(
        self,
        device: DeviceInfo,
        idx: int,
        phone_data_port: int,
        target_data_port: int,
        phone_signal_port: int,
        target_signal_port: int,
    ) -> None:
        """Create tunnels so phone can reach the host's bind port (SSH mode)."""
        win_rev_data = SWARM_SSH_REVERSE_BASE + idx
        proc = await ssh_reverse_tunnel(target_data_port, win_rev_data)
        if proc:
            self._ssh_reverse_procs.append(proc)
        await adb_reverse(device.serial, phone_data_port, win_rev_data)

        win_rev_sig = SWARM_SSH_REVERSE_BASE + self.SIGNAL_OFFSET + idx
        proc = await ssh_reverse_tunnel(target_signal_port, win_rev_sig)
        if proc:
            self._ssh_reverse_procs.append(proc)
        await adb_reverse(device.serial, phone_signal_port, win_rev_sig)

        logger.debug(
            "SSH outbound for {} (rank {}): phone:{} -> SSH -> localhost:{}",
            device.serial[:8], idx + 1,
            phone_data_port, target_data_port,
        )

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def teardown_ring(self) -> None:
        """Remove all tunnels and clean up."""
        # Kill SSH tunnel processes (SSH mode)
        for proc in self._ssh_forward_procs + self._ssh_reverse_procs:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()
        self._ssh_forward_procs.clear()
        self._ssh_reverse_procs.clear()

        # Remove adb forwards and reverses for all phones (SSH mode)
        if self._topology and self._topology.mode == "ssh":
            for node in self._topology.nodes:
                if node.serial:
                    from cellswarm.utils.adb import (
                        adb_forward_remove_all,
                        adb_reverse_remove_all,
                    )
                    await adb_forward_remove_all(node.serial)
                    await adb_reverse_remove_all(node.serial)

        self._topology = None
        logger.info("Ring topology torn down")

    @property
    def topology(self) -> RingTopology | None:
        return self._topology
