"""Tests for device discovery and probing."""

from __future__ import annotations

import pytest

from adb_llm.core.device import DeviceState
from adb_llm.core.device_manager import DeviceManager, _parse_df, _parse_meminfo, _parse_thermal
from adb_llm.core.errors import DeviceNotFoundError
from adb_llm.utils.adb import AdbDevice


def test_parse_meminfo():
    raw = """MemTotal:       11879856 kB
MemFree:          234564 kB
MemAvailable:    6012340 kB
Buffers:          123456 kB"""
    total, avail = _parse_meminfo(raw)
    assert total == 11879856 // 1024
    assert avail == 6012340 // 1024


def test_parse_df():
    raw = """Filesystem     1K-blocks    Used Available Use% Mounted on
/dev/block/dm-8  112233445 56677889  55555556  51% /data"""
    free = _parse_df(raw)
    assert free == 55555556 // 1024


def test_parse_thermal():
    assert _parse_thermal("38500") == 38.5
    assert _parse_thermal("42") == 42.0
    assert _parse_thermal("garbage") == 0.0


@pytest.mark.asyncio
async def test_discover(mock_adb, mock_devices):
    mock_adb["devices"].return_value = mock_devices

    dm = DeviceManager()
    devices = await dm.discover()
    assert len(devices) == 9
    assert all(d.state == DeviceState.CONNECTED for d in devices)


@pytest.mark.asyncio
async def test_resolve_targets_all(mock_adb, mock_devices):
    mock_adb["devices"].return_value = mock_devices

    dm = DeviceManager()
    await dm.discover()
    targets = dm.resolve_targets("all")
    assert len(targets) == 9


@pytest.mark.asyncio
async def test_resolve_targets_specific(mock_adb, mock_devices):
    mock_adb["devices"].return_value = mock_devices

    dm = DeviceManager()
    await dm.discover()
    targets = dm.resolve_targets("R5CR0001ABC,R5CR0003ABC")
    assert len(targets) == 2
    assert targets[0].serial == "R5CR0001ABC"


@pytest.mark.asyncio
async def test_resolve_targets_unknown(mock_adb, mock_devices):
    mock_adb["devices"].return_value = mock_devices

    dm = DeviceManager()
    await dm.discover()
    with pytest.raises(DeviceNotFoundError):
        dm.resolve_targets("NONEXISTENT")
