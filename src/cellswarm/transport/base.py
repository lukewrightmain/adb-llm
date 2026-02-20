"""Abstract transport layer interface.

This interface allows swapping transport implementations (adb forward, raw streams)
without changing inference orchestration code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from cellswarm.core.device import DeviceInfo


@dataclass
class TransportConnection:
    """An active transport connection to a device."""
    device: DeviceInfo
    local_port: int
    remote_port: int
    active: bool = True

    @property
    def local_endpoint(self) -> str:
        return f"localhost:{self.local_port}"


class TransportLayer(ABC):
    """Abstract interface for device-to-host transport."""

    @abstractmethod
    async def connect(self, device: DeviceInfo, remote_port: int) -> TransportConnection:
        """Establish transport to a device's remote port."""

    @abstractmethod
    async def disconnect(self, device: DeviceInfo) -> None:
        """Tear down transport for a device."""

    @abstractmethod
    async def disconnect_all(self) -> None:
        """Tear down all transports."""

    @abstractmethod
    async def health_check(self, device: DeviceInfo) -> bool:
        """Check if transport to device is healthy."""

    @abstractmethod
    async def list_connections(self) -> list[TransportConnection]:
        """List all active connections."""
