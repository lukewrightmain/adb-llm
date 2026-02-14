"""Shared test fixtures for adb-llm tests."""

from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from adb_llm.core.device import DeviceInfo, DeviceState
from adb_llm.utils.adb import AdbDevice

GGUF_MAGIC = 0x46554747


@pytest.fixture
def mock_devices() -> list[AdbDevice]:
    """9 mock ADB devices matching the phone cluster."""
    return [
        AdbDevice(serial=f"R5CR{i:04d}ABC", state="device", model="SM_F926U")
        for i in range(1, 10)
    ]


@pytest.fixture
def mock_device_infos() -> list[DeviceInfo]:
    """9 mock DeviceInfo objects with realistic data."""
    return [
        DeviceInfo(
            serial=f"R5CR{i:04d}ABC",
            state=DeviceState.READY,
            model="SM-F926U",
            total_ram_mb=12288,
            available_ram_mb=6000,
            storage_free_mb=50000,
            cpu_cores=8,
            cpu_arch="arm64-v8a",
            android_version="13",
            has_rpc_server=True,
            rpc_server_path="/data/local/tmp/adb-llm/bin/rpc-server",
        )
        for i in range(1, 10)
    ]


@pytest.fixture
def tmp_gguf(tmp_path: Path) -> Path:
    """Create a minimal valid GGUF file for testing."""
    path = tmp_path / "test-model.gguf"
    with open(path, "wb") as f:
        # Magic
        f.write(struct.pack("<I", GGUF_MAGIC))
        # Version 3
        f.write(struct.pack("<I", 3))
        # Tensor count
        f.write(struct.pack("<Q", 42))
        # Metadata KV count (0 for simplicity)
        f.write(struct.pack("<Q", 0))
        # Some padding to make it a reasonable size
        f.write(b"\x00" * 1024)
    return path


@pytest.fixture
def mock_adb():
    """Patch all ADB calls to be no-ops."""
    with (
        patch("adb_llm.utils.adb.adb_devices", new_callable=AsyncMock) as m_devices,
        patch("adb_llm.utils.adb.adb_shell", new_callable=AsyncMock) as m_shell,
        patch("adb_llm.utils.adb.adb_push", new_callable=AsyncMock) as m_push,
        patch("adb_llm.utils.adb.adb_forward", new_callable=AsyncMock) as m_forward,
    ):
        m_push.return_value = (1.0, 1e9)  # 1s, 1GB/s
        yield {
            "devices": m_devices,
            "shell": m_shell,
            "push": m_push,
            "forward": m_forward,
        }
