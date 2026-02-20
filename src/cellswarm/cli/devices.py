"""CLI commands for device management: list, probe, benchmark."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time

import click
from rich.console import Console
from rich.table import Table

from cellswarm.core.device import DeviceState
from cellswarm.core.device_manager import DeviceManager
from cellswarm.utils.adb import adb_push, adb_shell

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

        rpc = "[green]yes[/]" if dev.has_swarm_rpc else "[dim]no[/]"
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
        remote = "/data/local/tmp/cellswarm_bench.bin"
        try:
            elapsed, speed = await adb_push(dev.serial, tmp_path, remote)
            # Clean up
            await adb_shell(dev.serial, f"rm -f {remote}", check=False)
            return (dev.serial, elapsed, speed / 1e6, "")
        except Exception as e:
            return (dev.serial, 0.0, 0.0, str(e))

    return await asyncio.gather(*[_bench_one(d) for d in devices])


@devices.command()
@click.option("--skip-binary", is_flag=True, help="Skip pushing swarm-rpc binary")
def setup(skip_binary: bool) -> None:
    """Set up remote directories and push swarm-rpc binary to all devices."""
    dm = DeviceManager()
    devs = _run(dm.discover())
    online = [d for d in devs if d.state != DeviceState.OFFLINE]

    if not online:
        console.print("[yellow]No online devices found.[/]")
        return

    # Locate swarm-rpc binary
    rpc_binary = None
    if not skip_binary:
        from pathlib import Path
        from cellswarm.core.config import REMOTE_SWARM_RPC

        # Look in cellswarm/bin/ relative to the package
        candidates = [
            Path(__file__).resolve().parents[3] / "bin" / "swarm-rpc",
            Path.home() / "cellswarm" / "bin" / "swarm-rpc",
        ]
        for c in candidates:
            if c.is_file():
                rpc_binary = c
                break

        if rpc_binary is None:
            console.print(
                "[yellow]swarm-rpc binary not found. Run scripts/build_swarm_rpc.sh first.[/]"
            )
            console.print("[yellow]Skipping binary push, creating directories only.[/]")

    async def _setup_all():
        from cellswarm.core.config import REMOTE_MODELS_DIR, REMOTE_BIN_DIR, REMOTE_SWARM_RPC

        async def _setup_one(dev):
            try:
                await adb_shell(dev.serial, f"mkdir -p {REMOTE_MODELS_DIR}")
                await adb_shell(dev.serial, f"mkdir -p {REMOTE_BIN_DIR}")
                console.print(f"  [green]{dev.serial}[/]: directories created")

                if rpc_binary is not None:
                    console.print(f"  [cyan]{dev.serial}[/]: pushing swarm-rpc...")
                    await adb_push(dev.serial, str(rpc_binary), REMOTE_SWARM_RPC)
                    await adb_shell(dev.serial, f"chmod +x {REMOTE_SWARM_RPC}")
                    # Verify binary is executable (--help crashes on Android,
                    # so just check the file exists and is the right arch)
                    result = await adb_shell(
                        dev.serial, f"file {REMOTE_SWARM_RPC}",
                        timeout=10, check=False,
                    )
                    if "aarch64" in result.lower() or "arm" in result.lower() or "elf" in result.lower():
                        console.print(f"  [green]{dev.serial}[/]: swarm-rpc verified (ARM64)")
                    else:
                        console.print(f"  [yellow]{dev.serial}[/]: swarm-rpc pushed (could not verify arch)")
            except Exception as e:
                console.print(f"  [red]{dev.serial}[/]: {e}")

        # Run setup on all devices in parallel
        await asyncio.gather(*[_setup_one(d) for d in online])

    console.print(f"Setting up {len(online)} device(s)...")
    _run(_setup_all())
    console.print("[green]Setup complete.[/]")


@devices.command("add")
@click.argument("address")
@click.option("--label", default="", help="Friendly label for the device")
def add_device(address: str, label: str) -> None:
    """Add a device to the config and connect via adb.

    ADDRESS is IP or IP:PORT (default port: 5555).
    """
    from cellswarm.core.config import (
        DeviceEntry,
        load_config,
        save_config,
        reset_config,
    )

    # Parse address
    if ":" in address:
        ip, port_str = address.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 5555
    else:
        ip = address
        port = 5555

    serial = f"{ip}:{port}"

    cfg = load_config()

    # Check for duplicate
    for d in cfg.devices:
        if d.ip == ip and d.port == port:
            console.print(f"[yellow]Device {serial} already in config.[/]")
            return

    # Try adb connect (direct mode only)
    if cfg.mode == "direct":
        async def _connect():
            from cellswarm.utils.adb import _local_run
            try:
                stdout, _, _ = await _local_run(
                    [cfg.adb_bin, "connect", serial],
                    timeout=10, check=False,
                )
                return stdout
            except Exception as e:
                return str(e)

        result = _run(_connect())
        if "connected" in result.lower():
            console.print(f"[green]Connected to {serial}[/]")
        else:
            console.print(f"[yellow]adb connect {serial}: {result}[/]")

    cfg.devices.append(DeviceEntry(ip=ip, port=port, label=label))
    save_config(cfg)
    reset_config()
    console.print(f"[green]Added {serial} to config.[/]")


@devices.command("remove")
@click.argument("address")
def remove_device(address: str) -> None:
    """Remove a device from the config and disconnect.

    ADDRESS is IP or IP:PORT.
    """
    from cellswarm.core.config import load_config, save_config, reset_config

    if ":" in address:
        ip, port_str = address.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 5555
    else:
        ip = address
        port = 5555

    serial = f"{ip}:{port}"

    cfg = load_config()

    found = None
    for i, d in enumerate(cfg.devices):
        if d.ip == ip and d.port == port:
            found = i
            break

    if found is None:
        console.print(f"[yellow]Device {serial} not found in config.[/]")
        return

    # Try adb disconnect (direct mode only)
    if cfg.mode == "direct":
        async def _disconnect():
            from cellswarm.utils.adb import _local_run
            try:
                await _local_run(
                    [cfg.adb_bin, "disconnect", serial],
                    timeout=10, check=False,
                )
            except Exception:
                pass

        _run(_disconnect())

    cfg.devices.pop(found)
    save_config(cfg)
    reset_config()
    console.print(f"[green]Removed {serial} from config.[/]")
