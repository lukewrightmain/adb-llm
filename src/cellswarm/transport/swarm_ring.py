"""Ring topology transport for cellswarm pipeline-ring parallelism.

cellswarm uses ZMQ PUSH/PULL sockets in a ring:
  Host(rank0) -> Phone1(rank1) -> Phone2(rank2) -> ... -> Host(rank0)

Each phone needs:
  - Inbound (recv from predecessor): Phone binds PULL on data_port+rank.
    We reach it via: adb forward + SSH -L tunnel -> localhost on this server.
  - Outbound (send to successor): Phone connects PUSH to localhost:port,
    routed via: adb reverse + SSH -R tunnel -> next phone's bind port.

Optimization: For phone-to-phone connections (both source and dest are phones),
we bypass SSH entirely. Instead of:
  Phone1 -> adb reverse -> WinPC -> SSH-R -> Host -> SSH-L -> WinPC -> adb forward -> Phone2
We use:
  Phone1 -> adb reverse -> WinPC:forward_port -> adb forward -> Phone2
This eliminates 2 SSH hops (~10ms each) per inter-phone connection.

The host (rank 0) binds its own ZMQ sockets directly on localhost.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from loguru import logger

from cellswarm.core.config import (
    SWARM_DATA_PORT,
    SWARM_FORWARD_BASE,
    SWARM_REVERSE_BASE,
    SWARM_SIGNAL_PORT,
    SWARM_SSH_FORWARD_BASE,
    SWARM_SSH_REVERSE_BASE,
)
from cellswarm.core.device import DeviceInfo
from cellswarm.core.errors import TunnelError
from cellswarm.utils.adb import (
    adb_forward,
    adb_forward_remove,
    adb_reverse,
    adb_reverse_remove,
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
    # How to reach this node's bind ports from the host (localhost:X)
    host_reachable_data_port: int
    host_reachable_signal_port: int
    # WinPC-side ADB forward ports (used for direct phone-to-phone routing)
    winpc_fwd_data_port: int = 0
    winpc_fwd_signal_port: int = 0
    # What the phone sees as its next node address (localhost:X on phone)
    phone_next_data_port: int = 0    # Port phone connects to for PUSH to successor
    phone_next_signal_port: int = 0


@dataclass
class RingTopology:
    """Complete ring topology with tunnel metadata."""
    nodes: list[RingNode] = field(default_factory=list)
    world_size: int = 0

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


class SwarmRingTransport:
    """Manage the full ring of tunnels for cellswarm pipeline parallelism.

    The ring flows: Host(0) -> Phone1(1) -> Phone2(2) -> ... -> Host(0)

    Tunnel strategy:
      Host -> Phone1:  SSH -L tunnel (host must reach Phone1 via WinPC)
      PhoneN -> Host:  SSH -R tunnel + ADB reverse (PhoneN must reach host via WinPC)
      Phone -> Phone:  DIRECT via WinPC (ADB reverse -> WinPC port -> ADB forward)
                       No SSH tunnels needed — saves ~10ms per hop.
    """

    SIGNAL_OFFSET = 100  # Gap between data and signal port ranges

    def __init__(self) -> None:
        self._topology: RingTopology | None = None
        self._ssh_forward_procs: list[asyncio.subprocess.Process] = []
        self._ssh_reverse_procs: list[asyncio.subprocess.Process] = []

    async def setup_ring(self, devices: list[DeviceInfo]) -> RingTopology:
        """Build the full ring topology and create all tunnels.

        Args:
            devices: Phones to include (will become ranks 1..N).

        Returns:
            RingTopology with tunnel info for each node.
        """
        world_size = len(devices) + 1  # +1 for host at rank 0
        topology = RingTopology(world_size=world_size)

        # Host node (rank 0) — binds directly on localhost
        host_node = RingNode(
            rank=0,
            device=None,
            serial="",
            data_bind_port=SWARM_DATA_PORT,          # 9000
            signal_bind_port=SWARM_SIGNAL_PORT,       # 10000
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
                # SSH -L local port must match cellswarm's expected port (data_port + rank)
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

            # Phone connects PUSH to localhost:(DATA_PORT + next_rank)
            phone_next_data = SWARM_DATA_PORT + next_rank
            phone_next_signal = SWARM_SIGNAL_PORT + next_rank
            topology.nodes[rank].phone_next_data_port = phone_next_data
            topology.nodes[rank].phone_next_signal_port = phone_next_signal

            # Determine if this is a phone-to-phone or phone-to-host connection
            is_next_phone = next_node.serial != ""  # next is a phone, not host

            try:
                if is_next_phone:
                    # DIRECT: Phone -> WinPC -> Phone (no SSH)
                    await self._create_direct_outbound(
                        device, idx,
                        phone_next_data, next_node.winpc_fwd_data_port,
                        phone_next_signal, next_node.winpc_fwd_signal_port,
                    )
                else:
                    # Phone -> Host: needs SSH -R tunnel
                    await self._create_outbound_tunnels(
                        device, idx,
                        phone_next_data, next_node.host_reachable_data_port,
                        phone_next_signal, next_node.host_reachable_signal_port,
                    )
            except Exception as e:
                raise TunnelError(device.serial, f"Outbound tunnel failed: {e}") from e

        self._topology = topology

        logger.info("Ring topology established: {} nodes", world_size)
        for node in topology.nodes:
            label = "HOST" if node.rank == 0 else node.serial[:8]
            logger.info(
                "  rank {} ({}): data_bind={}, reachable=localhost:{}",
                node.rank, label, node.data_bind_port, node.host_reachable_data_port,
            )

        return topology

    async def _create_inbound_tunnels(
        self, device: DeviceInfo, idx: int, node: RingNode,
    ) -> None:
        """Create tunnels so that host can reach phone's ZMQ bind ports."""
        # Data port
        win_fwd_port = SWARM_FORWARD_BASE + idx
        await adb_forward(device.serial, win_fwd_port, node.data_bind_port)
        proc = await ssh_tunnel(node.host_reachable_data_port, win_fwd_port)
        if proc:
            self._ssh_forward_procs.append(proc)

        # Signal port
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
        """Create DIRECT phone-to-phone tunnels via WinPC (no SSH).

        Chain: phone:localhost:phone_port -> adb reverse -> winpc:fwd_port
               -> adb forward (already set up) -> next_phone:bind_port

        This bypasses SSH entirely for inter-phone connections, saving ~20ms
        per hop (2 SSH traversals eliminated).
        """
        # Data outbound — point ADB reverse directly at next phone's ADB forward port
        await adb_reverse(device.serial, phone_data_port, winpc_fwd_data_port)

        # Signal outbound — same direct routing
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
        """Create tunnels so phone can reach the host's bind port.

        Chain: phone:localhost:phone_port -> adb reverse -> winpc:ssh_rev_port
               -> SSH -R -> this server:target_port (host's bind port)

        Only used for PhoneN -> Host (rank 0) connection.
        """
        # Data outbound
        win_rev_data = SWARM_SSH_REVERSE_BASE + idx
        proc = await ssh_reverse_tunnel(target_data_port, win_rev_data)
        self._ssh_reverse_procs.append(proc)
        await adb_reverse(device.serial, phone_data_port, win_rev_data)

        # Signal outbound
        win_rev_sig = SWARM_SSH_REVERSE_BASE + self.SIGNAL_OFFSET + idx
        proc = await ssh_reverse_tunnel(target_signal_port, win_rev_sig)
        self._ssh_reverse_procs.append(proc)
        await adb_reverse(device.serial, phone_signal_port, win_rev_sig)

        logger.debug(
            "SSH outbound for {} (rank {}): phone:{} -> SSH -> localhost:{}",
            device.serial[:8], idx + 1,
            phone_data_port, target_data_port,
        )

    async def teardown_ring(self) -> None:
        """Remove all tunnels and clean up."""
        # Kill all SSH tunnel processes
        for proc in self._ssh_forward_procs + self._ssh_reverse_procs:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()
        self._ssh_forward_procs.clear()
        self._ssh_reverse_procs.clear()

        # Remove adb forwards and reverses for all phones
        if self._topology:
            for node in self._topology.nodes:
                if node.serial:  # skip host
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
