"""CLI commands for device management: list, probe, benchmark."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time

import click
from rich.console import Console
from rich.table import Table

from adb_llm.core.device import DeviceState
from adb_llm.core.device_manager import DeviceManager
from adb_llm.utils.adb import adb_push, adb_shell

console = Console()


def _run(coro):
    """Run an async coroutine from sync Click handler."""
    return asyncio.get_event_loop().run_until_complete(coro)


@click.group()
def devices() -> None:
    """Manage connected ADB devices."""


@devices.command("list")
def list_devices() -> None:
    """List all connected ADB devices."""
    dm = DeviceManager()
    devs = _run(dm.discover())

    if not devs:
        console.print("[yellow]No devices found. Check USB connections and `adb devices`.[/]")
        return

    table = Table(title=f"ADB Devices ({len(devs)} found)")
    table.add_column("Serial", style="cyan")
    table.add_column("State", style="bold")
    table.add_column("Model")
    table.add_column("USB")
    table.add_column("Transport")

    for dev in devs:
        state_style = "green" if dev.state == DeviceState.CONNECTED else "red"
        table.add_row(
            dev.serial,
            f"[{state_style}]{dev.state.value}[/]",
            dev.model,
            dev.usb,
            dev.transport_id,
        )

    console.print(table)


@devices.command()
def probe() -> None:
    """Deep probe all devices (RAM, storage, thermal, llama.cpp)."""
    dm = DeviceManager()
    _run(dm.discover())
    devs = _run(dm.probe_all())

    if not devs:
        console.print("[yellow]No devices found.[/]")
        return

    table = Table(title=f"Device Probe ({len(devs)} devices)")
    table.add_column("Serial", style="cyan")
    table.add_column("Model")
    table.add_column("State", style="bold")
    table.add_column("RAM (Total)", justify="right")
    table.add_column("RAM (Avail)", justify="right")
    table.add_column("Storage Free", justify="right")
    table.add_column("CPUs", justify="right")
    table.add_column("Arch")
    table.add_column("Android")
    table.add_column("Temp", justify="right")
    table.add_column("RPC")
    table.add_column("Models", justify="right")

    for dev in devs:
        state_style = {
            DeviceState.READY: "green",
            DeviceState.ERROR: "red",
            DeviceState.OFFLINE: "dim",
        }.get(dev.state, "yellow")

        rpc = "[green]yes[/]" if dev.has_rpc_server else "[dim]no[/]"
        temp = f"{dev.thermal_temp_c:.0f}C" if dev.thermal_temp_c else "-"

        table.add_row(
            dev.short_serial,
            dev.model,
            f"[{state_style}]{dev.state.value}[/]",
            f"{dev.total_ram_mb} MB",
            f"{dev.available_ram_mb} MB",
            f"{dev.storage_free_mb} MB",
            str(dev.cpu_cores),
            dev.cpu_arch,
            dev.android_version,
            temp,
            rpc,
            str(len(dev.models)),
        )

    console.print(table)


@devices.command()
@click.option("--size-mb", default=100, help="Test file size in MB")
def benchmark(size_mb: int) -> None:
    """Benchmark ADB push speed to each device."""
    dm = DeviceManager()
    devs = _run(dm.discover())
    online = [d for d in devs if d.state != DeviceState.OFFLINE]

    if not online:
        console.print("[yellow]No online devices found.[/]")
        return

    console.print(f"Benchmarking with {size_mb} MB test file on {len(online)} device(s)...\n")

    # Create a temporary test file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        tmp.write(os.urandom(size_mb * 1024 * 1024))
        tmp_path = tmp.name

    try:
        results = _run(_bench_all(online, tmp_path, size_mb))
    finally:
        os.unlink(tmp_path)

    table = Table(title="ADB Push Benchmark")
    table.add_column("Serial", style="cyan")
    table.add_column("Speed", justify="right", style="bold")
    table.add_column("Time", justify="right")
    table.add_column("Status")

    for serial, elapsed, speed_mbps, err in results:
        if err:
            table.add_row(serial[:16], "-", "-", f"[red]{err}[/]")
        else:
            table.add_row(
                serial[:16],
                f"{speed_mbps:.0f} MB/s",
                f"{elapsed:.1f}s",
                "[green]OK[/]",
            )

    console.print(table)


async def _bench_all(
    devices, tmp_path: str, size_mb: int
) -> list[tuple[str, float, float, str]]:
    """Benchmark push speed on all devices in parallel."""

    async def _bench_one(dev):
        remote = "/data/local/tmp/adb_llm_bench.bin"
        try:
            elapsed, speed = await adb_push(dev.serial, tmp_path, remote)
            # Clean up
            await adb_shell(dev.serial, f"rm -f {remote}", check=False)
            return (dev.serial, elapsed, speed / 1e6, "")
        except Exception as e:
            return (dev.serial, 0.0, 0.0, str(e))

    return await asyncio.gather(*[_bench_one(d) for d in devices])


@devices.command()
def setup() -> None:
    """Set up remote directories and check prerequisites on all devices."""
    dm = DeviceManager()
    devs = _run(dm.discover())
    online = [d for d in devs if d.state != DeviceState.OFFLINE]

    if not online:
        console.print("[yellow]No online devices found.[/]")
        return

    async def _setup_all():
        from adb_llm.core.config import REMOTE_BASE, REMOTE_MODELS_DIR, REMOTE_BIN_DIR

        for dev in online:
            try:
                await adb_shell(dev.serial, f"mkdir -p {REMOTE_MODELS_DIR}")
                await adb_shell(dev.serial, f"mkdir -p {REMOTE_BIN_DIR}")
                console.print(f"  [green]{dev.serial}[/]: directories created")
            except Exception as e:
                console.print(f"  [red]{dev.serial}[/]: {e}")

    _run(_setup_all())
    console.print("[green]Setup complete.[/]")
