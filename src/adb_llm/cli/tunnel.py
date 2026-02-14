"""CLI commands for USB tunnel management."""

from __future__ import annotations

import asyncio

import click
from rich.console import Console
from rich.table import Table

from adb_llm.core.config import RPC_BASE_LOCAL_PORT, RPC_REMOTE_PORT
from adb_llm.core.device_manager import DeviceManager
from adb_llm.transport.adb_forward import AdbForwardTransport
from adb_llm.transport.health import HealthMonitor

console = Console()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@click.group()
def tunnel() -> None:
    """Manage USB tunnels (adb forward) to devices."""


@tunnel.command()
@click.option("--base-port", default=RPC_BASE_LOCAL_PORT, help="Starting local port")
@click.option("--remote-port", default=RPC_REMOTE_PORT, help="Remote rpc-server port")
@click.option("--devices", "target", default="all", help="Target devices")
def create(base_port: int, remote_port: int, target: str) -> None:
    """Create USB tunnels to all devices."""

    async def _create():
        dm = DeviceManager()
        await dm.discover()
        devices = dm.resolve_targets(target)

        if not devices:
            console.print("[yellow]No devices found.[/]")
            return

        transport = AdbForwardTransport(base_port=base_port)

        console.print(f"Creating tunnels to {len(devices)} device(s)...\n")
        for dev in devices:
            try:
                conn = await transport.connect(dev, remote_port)
                console.print(f"  [green]{dev.serial}[/] -> {conn.local_endpoint}")
            except Exception as e:
                console.print(f"  [red]{dev.serial}[/]: {e}")

        console.print(f"\n[bold]RPC arg:[/] --rpc {transport.get_rpc_arg()}")

    _run(_create())


@tunnel.command()
def status() -> None:
    """Show tunnel health for all devices."""

    async def _status():
        dm = DeviceManager()
        await dm.discover()
        await dm.probe_all()
        devices = list(dm.devices.values())

        if not devices:
            console.print("[yellow]No devices found.[/]")
            return

        transport = AdbForwardTransport()
        monitor = HealthMonitor(transport)
        reports = await monitor.check_all(devices)

        table = Table(title="Tunnel Status")
        table.add_column("Serial", style="cyan")
        table.add_column("USB")
        table.add_column("RPC Server")
        table.add_column("Tunnel")
        table.add_column("Temp")
        table.add_column("Status", style="bold")

        for report in reports:
            usb = "[green]ok[/]" if report.usb_connected else "[red]down[/]"
            rpc = "[green]running[/]" if report.rpc_server_running else "[dim]stopped[/]"
            tun = "[green]alive[/]" if report.tunnel_alive else "[dim]none[/]"
            temp = f"{report.thermal_temp_c:.0f}C" if report.thermal_temp_c else "-"
            status_style = {"healthy": "green", "degraded": "yellow", "down": "red"}.get(
                report.status, "white"
            )
            table.add_row(
                report.serial[:16],
                usb, rpc, tun, temp,
                f"[{status_style}]{report.status}[/]",
            )

        console.print(table)

    _run(_status())


@tunnel.command()
@click.option("--devices", "target", default="all", help="Target devices")
def destroy(target: str) -> None:
    """Remove all USB tunnels."""

    async def _destroy():
        dm = DeviceManager()
        await dm.discover()
        devices = dm.resolve_targets(target)

        transport = AdbForwardTransport()
        for dev in devices:
            await transport.disconnect(dev)
            console.print(f"  Removed tunnel for {dev.serial}")

        console.print("[green]All tunnels removed.[/]")

    _run(_destroy())
