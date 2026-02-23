"""Ethernet ring transport for cellswarm pipeline-ring parallelism.

For phones connected via ethernet (ADB over TCP/IP at IP:5555), phones are
directly reachable by IP from the host. However, the reverse direction
(phone → host) may be blocked by network firewalls. We use ADB reverse
tunnels so phones can reach the host at 127.0.0.1.

Ring topology:
  Host(rank0) → Phone1(rank1) → Phone2(rank2) → ... → Host(rank0)

Each phone binds ZMQ PULL on 0.0.0.0:(data_port+rank) and connects
ZMQ PUSH directly to the next node's IP:(data_port+next_rank).
The last phone connects back to the host via ADB reverse tunnel
(127.0.0.1:data_port → host:data_port).
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field

from loguru import logger

from cellswarm.core.config import SWARM_DATA_PORT, SWARM_SIGNAL_PORT
from cellswarm.core.device import DeviceInfo
from cellswarm.core.errors import TunnelError
from cellswarm.utils.adb import adb_reverse, adb_reverse_remove_all, adb_shell


def _serial_to_ip(serial: str) -> str:
    """Extract IP from an ethernet serial like '10.105.0.42:5555'."""
    if ":" in serial:
        return serial.split(":")[0]
    return serial


def _get_host_ip(devices: list[DeviceInfo]) -> str:
    """Determine host IP reachable from the phone subnet.

    Opens a UDP socket toward the first phone's IP to discover which
    local interface is on the same network.
    """
    if not devices:
        return "127.0.0.1"
    target_ip = _serial_to_ip(devices[0].serial)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target_ip, 1))
        host_ip = s.getsockname()[0]
        s.close()
        return host_ip
    except Exception:
        return "127.0.0.1"


@dataclass
class EthernetRingNode:
    """One node in the ethernet ring topology.

    Compatible with RingNode interface so SwarmManager can use it.
    """
    rank: int
    device: DeviceInfo | None  # None for host (rank 0)
    serial: str
    ip: str                        # Direct IP address
    data_bind_port: int            # Port this node binds PULL on
    signal_bind_port: int          # Port this node binds PULL on (signal)
    # How to reach this node from the host
    host_reachable_data_port: int
    host_reachable_signal_port: int
    # What the phone connects to for its PUSH to successor
    phone_next_data_port: int = 0
    phone_next_signal_port: int = 0
    next_ip: str = ""              # IP of next node in ring
    master_ip: str = ""            # IP of rank 0 (host)


@dataclass
class EthernetRingTopology:
    """Complete ethernet ring topology — no tunnels needed."""
    nodes: list[EthernetRingNode] = field(default_factory=list)
    world_size: int = 0

    def get_node(self, rank: int) -> EthernetRingNode:
        return self.nodes[rank]

    def layer_assignment(self, total_layers: int) -> list[int]:
        """Divide layers evenly across all nodes (host + phones).

        Same distribution as the bench scripts: every node gets
        total_layers // world_size, with remainder distributed to
        the first few nodes.
        """
        base = total_layers // self.world_size
        remainder = total_layers % self.world_size
        layers: list[int] = []
        for i in range(self.world_size):
            extra = 1 if i < remainder else 0
            layers.append(base + extra)
        return layers


class EthernetRingTransport:
    """Ring transport for ethernet-connected phones.

    Host → phones: direct IP (phones are reachable by their ethernet IP).
    Phones → host: ADB reverse tunnels (phone:127.0.0.1:port → host:port)
    because direct TCP from phone to host may be blocked by firewall.
    """

    def __init__(self) -> None:
        self._topology: EthernetRingTopology | None = None

    async def setup_ring(self, devices: list[DeviceInfo]) -> EthernetRingTopology:
        """Build ring topology with direct IP addresses.

        Args:
            devices: Ethernet phones (ranks 1..N).

        Returns:
            EthernetRingTopology with direct IP routing.
        """
        world_size = len(devices) + 1  # +1 for host at rank 0
        host_ip = _get_host_ip(devices)
        topology = EthernetRingTopology(world_size=world_size)

        # Host node (rank 0)
        host_node = EthernetRingNode(
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
        # NOTE: All nodes use the same base port. The C++ code internally
        # calculates bind_port = data_port + rank, so we pass the same
        # base port (SWARM_DATA_PORT) to every node — NOT data_port + rank.
        for idx, device in enumerate(devices):
            rank = idx + 1
            phone_ip = _serial_to_ip(device.serial)
            node = EthernetRingNode(
                rank=rank,
                device=device,
                serial=device.serial,
                ip=phone_ip,
                data_bind_port=SWARM_DATA_PORT,
                signal_bind_port=SWARM_SIGNAL_PORT,
                # Host reaches phone directly by IP
                host_reachable_data_port=SWARM_DATA_PORT + rank,
                host_reachable_signal_port=SWARM_SIGNAL_PORT + rank,
            )
            topology.nodes.append(node)

        # Set next_ip and master_ip for each node.
        # Phones can't reach host via direct IP (firewall), so they use
        # ADB reverse tunnels: phone's 127.0.0.1:port → host:port.
        # Host reaches phones by direct IP (no tunnel needed).
        for idx in range(world_size):
            next_rank = (idx + 1) % world_size
            next_node = topology.nodes[next_rank]
            if idx == 0:
                # Host: connects to phone1 by direct IP
                topology.nodes[idx].next_ip = next_node.ip
                topology.nodes[idx].master_ip = host_ip
            else:
                # Phone: next is either another phone (direct IP) or host (reverse tunnel)
                if next_rank == 0:
                    # Last phone → host: use reverse tunnel
                    topology.nodes[idx].next_ip = "127.0.0.1"
                else:
                    # Phone → phone: direct IP works
                    topology.nodes[idx].next_ip = next_node.ip
                # Master (host) always via reverse tunnel
                topology.nodes[idx].master_ip = "127.0.0.1"
            topology.nodes[idx].phone_next_data_port = next_node.data_bind_port
            topology.nodes[idx].phone_next_signal_port = next_node.signal_bind_port

        # Verify connectivity: host → phones (direct IP)
        logger.info("Verifying ethernet connectivity to {} phones...", len(devices))
        await self._verify_connectivity(devices, topology)

        # Set up ADB reverse tunnels: phone:127.0.0.1:port → host:port
        # Phones need to reach host's ZMQ data port (9000) and signal port (10000)
        logger.info("Setting up ADB reverse tunnels for phone→host connectivity...")
        await self._setup_reverse_tunnels(devices)

        self._topology = topology

        logger.info("Ethernet ring established: {} nodes (host={})", world_size, host_ip)
        for node in topology.nodes:
            label = f"HOST({host_ip})" if node.rank == 0 else f"{node.ip}"
            logger.info(
                "  rank {} ({}): bind={}:{}, next={}:{}",
                node.rank, label,
                node.ip, node.data_bind_port,
                node.next_ip, node.phone_next_data_port,
            )

        return topology

    async def _verify_connectivity(
        self, devices: list[DeviceInfo], topology: EthernetRingTopology,
    ) -> None:
        """Verify we can reach each phone via TCP."""
        for node in topology.nodes:
            if node.rank == 0:
                continue
            ip = node.ip
            try:
                # Quick TCP connect test
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, 5555),
                    timeout=3,
                )
                writer.close()
                await writer.wait_closed()
                logger.info("  {} (rank {}): reachable", ip, node.rank)
            except Exception:
                logger.warning("  {} (rank {}): NOT reachable via TCP", ip, node.rank)

    async def _setup_reverse_tunnels(self, devices: list[DeviceInfo]) -> None:
        """Set up ADB reverse tunnels so phones can reach the host.

        Each phone gets reverse tunnels for the host's ZMQ ports:
          phone:127.0.0.1:SWARM_DATA_PORT → host:SWARM_DATA_PORT
          phone:127.0.0.1:SWARM_SIGNAL_PORT → host:SWARM_SIGNAL_PORT
        """
        for device in devices:
            serial = device.serial
            try:
                await adb_reverse(serial, SWARM_DATA_PORT, SWARM_DATA_PORT)
                await adb_reverse(serial, SWARM_SIGNAL_PORT, SWARM_SIGNAL_PORT)
                logger.info("  {} reverse tunnels: :{},:{} → host",
                            serial.split(":")[0], SWARM_DATA_PORT, SWARM_SIGNAL_PORT)
            except Exception as e:
                logger.warning("  {} reverse tunnel setup failed: {}", serial.split(":")[0], e)

    async def _teardown_reverse_tunnels(self, devices: list[DeviceInfo]) -> None:
        """Remove ADB reverse tunnels."""
        for device in devices:
            try:
                await adb_reverse_remove_all(device.serial)
            except Exception:
                pass

    async def teardown_ring(self) -> None:
        """Clean up — remove ADB reverse tunnels."""
        if self._topology:
            devices = [n.device for n in self._topology.nodes if n.device is not None]
            await self._teardown_reverse_tunnels(devices)
        self._topology = None
        logger.info("Ethernet ring topology cleared")

    @property
    def topology(self) -> EthernetRingTopology | None:
        return self._topology
