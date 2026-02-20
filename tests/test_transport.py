"""Tests for transport layer."""

from __future__ import annotations

import pytest

from cellswarm.core.device import DeviceInfo, DeviceState
from cellswarm.transport.adb_forward import AdbForwardTransport


@pytest.mark.asyncio
async def test_connect_creates_forward(mock_adb):
    dev = DeviceInfo(serial="R5CR0001ABC", state=DeviceState.READY)
    transport = AdbForwardTransport(base_port=60001)

    conn = await transport.connect(dev, remote_port=60000)
    assert conn.local_port == 60001
    assert conn.local_endpoint == "localhost:60001"
    assert conn.active is True
    mock_adb["forward"].assert_called_once_with("R5CR0001ABC", 60001, 60000)


@pytest.mark.asyncio
async def test_connect_increments_ports(mock_adb):
    transport = AdbForwardTransport(base_port=60001)
    devs = [
        DeviceInfo(serial=f"R5CR{i:04d}ABC", state=DeviceState.READY)
        for i in range(1, 4)
    ]

    conns = []
    for dev in devs:
        conns.append(await transport.connect(dev, 60000))

    assert [c.local_port for c in conns] == [60001, 60002, 60003]


@pytest.mark.asyncio
async def test_connect_idempotent(mock_adb):
    dev = DeviceInfo(serial="R5CR0001ABC", state=DeviceState.READY)
    transport = AdbForwardTransport(base_port=60001)

    conn1 = await transport.connect(dev, 60000)
    conn2 = await transport.connect(dev, 60000)
    assert conn1 is conn2
    assert mock_adb["forward"].call_count == 1  # Only called once


@pytest.mark.asyncio
async def test_rpc_arg(mock_adb):
    transport = AdbForwardTransport(base_port=60001)
    devs = [
        DeviceInfo(serial=f"R5CR{i:04d}ABC", state=DeviceState.READY)
        for i in range(1, 4)
    ]
    for dev in devs:
        await transport.connect(dev, 60000)

    assert transport.get_rpc_arg() == "localhost:60001,localhost:60002,localhost:60003"


@pytest.mark.asyncio
async def test_disconnect_all(mock_adb):
    transport = AdbForwardTransport(base_port=60001)
    devs = [
        DeviceInfo(serial=f"R5CR{i:04d}ABC", state=DeviceState.READY)
        for i in range(1, 3)
    ]
    for dev in devs:
        await transport.connect(dev, 60000)

    await transport.disconnect_all()
    conns = await transport.list_connections()
    assert len(conns) == 0
