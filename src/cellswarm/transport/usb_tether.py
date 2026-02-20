"""USB Tethering transport for cellswarm pipeline-ring parallelism.

Replaces ADB tunnel chains with direct TCP/IP over USB by enabling
RNDIS/NCM tethering on each phone. Each phone gets a USB network
interface visible to WinPC, enabling standard TCP routing without
the ADB protocol overhead.

Expected latency improvement: 10-20ms/hop (ADB) → 0.5-2ms/hop (tethering).

Topology (same ring as swarm_ring.py):
  Phone0(rank0) → Phone1(rank1) → ... → PhoneN(rankN) → Phone0(rank0)

But instead of:
  Phone → adb reverse → WinPC → [SSH] → WinPC → adb forward → Phone
We use:
  Phone → TCP connect to next phone's tether IP directly

WinPC acts as a Layer 3 router between phone USB subnets, or all
phones share a flat 10.0.0.0/24 subnet via WinPC bridge.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from loguru import logger

from cellswarm.core.config import SWARM_DATA_PORT, SWARM_SIGNAL_PORT
from cellswarm.core.device import DeviceInfo
from cellswarm.core.errors import TunnelError
from cellswarm.utils.adb import (
    _ssh_run,
    adb_shell,
    enable_usb_tethering,
    disable_usb_tethering,
    get_tether_ip,
    set_tether_ip,
    open_tether_firewall,
)


# Tethering IP subnet: 10.0.0.0/24
# Phone rank N gets IP 10.0.0.(N+1)
TETHER_SUBNET = "10.0.0"
TETHER_IFACE = "rndis0"  # or ncm0 on Android 14+


@dataclass
class TetherNode:
    """One node in the USB-tethered ring topology."""
    rank: int
    device: DeviceInfo
    serial: str
    tether_ip: str                 # Phone's IP on tethering interface
    data_bind_port: int            # Port this node binds PULL on
    signal_bind_port: int          # Port this node binds PULL on (signal)
    next_ip: str = ""              # IP of next node in ring
    master_ip: str = ""            # IP of rank 0 (for master_socket)


@dataclass
class TetherTopology:
    """Complete USB-tethered ring topology."""
    nodes: list[TetherNode] = field(default_factory=list)
    world_size: int = 0
    tether_mode: str = "rndis"     # "rndis" or "ncm"
    winpc_bridge: bool = False     # True if WinPC bridges subnets

    def get_node(self, rank: int) -> TetherNode:
        return self.nodes[rank]

    def layer_assignment(self, total_layers: int) -> list[int]:
        """Divide layers evenly across phones."""
        n = self.world_size
        if n <= 0:
            return [total_layers]
        base = total_layers // n
        remainder = total_layers % n
        layers = []
        for i in range(n):
            extra = 1 if i < remainder else 0
            layers.append(base + extra)
        return layers


class UsbTetherTransport:
    """Manage USB tethering network for cellswarm phone-only ring.

    This transport enables RNDIS/NCM tethering on each phone, assigns
    static IPs, and configures the ring so phones communicate directly
    over TCP/IP through the USB network interfaces.
    """

    def __init__(self, tether_mode: str = "rndis") -> None:
        self._topology: TetherTopology | None = None
        self._tether_mode = tether_mode

    async def setup_ring(self, devices: list[DeviceInfo]) -> TetherTopology:
        """Enable tethering on all phones and build ring topology.

        Args:
            devices: Phones to include (ranks 0..N-1).

        Returns:
            TetherTopology with direct IP addresses for each node.
        """
        world_size = len(devices)
        topology = TetherTopology(
            world_size=world_size,
            tether_mode=self._tether_mode,
        )

        # Step 1: Enable tethering on all phones
        logger.info("Enabling USB tethering on {} phones (mode={})...", world_size, self._tether_mode)
        tether_results = await asyncio.gather(*[
            enable_usb_tethering(d.serial, self._tether_mode)
            for d in devices
        ])

        failed = [devices[i].serial[:8] for i, ok in enumerate(tether_results) if not ok]
        if failed:
            raise TunnelError(
                ",".join(failed),
                f"USB tethering failed on: {', '.join(failed)}",
            )

        # Step 2: Assign static IPs and build nodes
        for idx, device in enumerate(devices):
            ip = f"{TETHER_SUBNET}.{idx + 1}"
            iface = "ncm0" if self._tether_mode == "ncm" else TETHER_IFACE

            ok = await set_tether_ip(device.serial, ip, iface)
            if not ok:
                raise TunnelError(device.serial, f"Failed to set tether IP {ip}")

            # Open firewall for ZMQ ports
            await open_tether_firewall(device.serial, "9000:10100", iface)

            node = TetherNode(
                rank=idx,
                device=device,
                serial=device.serial,
                tether_ip=ip,
                data_bind_port=SWARM_DATA_PORT + idx,
                signal_bind_port=SWARM_SIGNAL_PORT + idx,
            )
            topology.nodes.append(node)

        # Step 3: Set next_ip and master_ip for each node
        for idx in range(world_size):
            next_rank = (idx + 1) % world_size
            topology.nodes[idx].next_ip = topology.nodes[next_rank].tether_ip
            topology.nodes[idx].master_ip = topology.nodes[0].tether_ip

        # Step 4: Verify connectivity (ping test)
        logger.info("Testing tethering connectivity...")
        await self._verify_connectivity(devices, topology)

        self._topology = topology

        logger.info("USB tethering ring established: {} phones", world_size)
        for node in topology.nodes:
            logger.info(
                "  rank {} ({}): ip={}, next_ip={}, data_port={}",
                node.rank, node.serial[:8], node.tether_ip,
                node.next_ip, node.data_bind_port,
            )

        return topology

    async def _verify_connectivity(
        self, devices: list[DeviceInfo], topology: TetherTopology,
    ) -> None:
        """Ping each phone from WinPC to verify tethering works."""
        for node in topology.nodes:
            try:
                # Ping from WinPC (which has the USB network interfaces)
                stdout, _, rc = await _ssh_run(
                    f'ping -n 1 -w 2000 {node.tether_ip}',
                    timeout=10,
                    check=False,
                )
                if rc == 0:
                    logger.info("  {} ({}): ping OK", node.serial[:8], node.tether_ip)
                else:
                    logger.warning(
                        "  {} ({}): ping FAILED (rc={}). "
                        "WinPC may need to configure the USB Ethernet adapter.",
                        node.serial[:8], node.tether_ip, rc,
                    )
            except Exception as e:
                logger.warning("  {} ({}): ping error: {}", node.serial[:8], node.tether_ip, e)

    async def teardown_ring(self) -> None:
        """Disable tethering on all phones and clean up."""
        if self._topology:
            await asyncio.gather(*[
                disable_usb_tethering(node.serial)
                for node in self._topology.nodes
            ])
        self._topology = None
        logger.info("USB tethering ring torn down")

    @property
    def topology(self) -> TetherTopology | None:
        return self._topology
