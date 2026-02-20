"""Device data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DeviceState(str, Enum):
    CONNECTED = "connected"  # ADB sees it, not yet probed
    READY = "ready"          # Probed, healthy, can accept work
    BUSY = "busy"            # Currently running inference / push
    ERROR = "error"          # Probe failed or unhealthy
    OFFLINE = "offline"      # ADB reports offline / not found


@dataclass
class DeviceInfo:
    serial: str
    state: DeviceState = DeviceState.CONNECTED

    # From ADB device listing
    usb: str = ""
    product: str = ""
    model: str = ""
    transport_id: str = ""

    # From probe
    total_ram_mb: int = 0
    available_ram_mb: int = 0
    storage_free_mb: int = 0
    cpu_cores: int = 0
    cpu_arch: str = ""
    android_version: str = ""
    thermal_temp_c: float = 0.0

    # llama.cpp state
    has_swarm_rpc: bool = False
    swarm_rpc_path: str = ""

    # Models present on device
    models: list[str] = field(default_factory=list)

    @property
    def usable_ram_mb(self) -> int:
        """Approximate RAM available for inference (~50% of total)."""
        return self.available_ram_mb if self.available_ram_mb else self.total_ram_mb // 2

    @property
    def short_serial(self) -> str:
        """Shortened serial for display."""
        if len(self.serial) > 12:
            return self.serial[:4] + ".." + self.serial[-4:]
        return self.serial
