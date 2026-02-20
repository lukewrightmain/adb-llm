"""Interactive setup wizard: `cellswarm init`."""

from __future__ import annotations

import asyncio

import click
from rich.console import Console
from rich.table import Table

from cellswarm.core.config import (
    ConnectionConfig,
    DeviceEntry,
    ensure_dirs,
    save_config,
    reset_config,
    CONFIG_FILE,
)

console = Console()


def _run(coro):
    """Run an async coroutine from sync Click handler."""
    return asyncio.get_event_loop().run_until_complete(coro)


async def _discover_devices_direct(adb_bin: str) -> list[DeviceEntry]:
    """Scan for devices using local ADB."""
    proc = await asyncio.create_subprocess_exec(
        adb_bin, "devices", "-l",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    output = stdout.decode(errors="replace")

    entries: list[DeviceEntry] = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        state = parts[1]
        if state != "device":
            continue

        # Parse attributes
        attrs: dict[str, str] = {}
        for part in parts[2:]:
            if ":" in part:
                k, v = part.split(":", 1)
                attrs[k] = v

        model = attrs.get("model", "")

        # TCP/IP device: serial is IP:PORT
        if ":" in serial:
            ip, port_str = serial.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 5555
            entries.append(DeviceEntry(ip=ip, port=port, label=model))
        else:
            # USB device — not a TCP/IP device, note it but skip
            console.print(
                f"  [dim]Skipping USB device {serial} "
                f"({model or 'unknown'}) — direct mode needs TCP/IP devices[/]"
            )

    return entries


async def _probe_device_info(adb_bin: str, serial: str) -> str:
    """Get brief info (model, RAM) for a device."""
    proc = await asyncio.create_subprocess_exec(
        adb_bin, "-s", serial, "shell",
        "getprop ro.product.model && cat /proc/meminfo | grep MemAvailable",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    return stdout.decode(errors="replace").strip()


async def _discover_devices_ssh(ssh_host: str, remote_adb: str) -> list[DeviceEntry]:
    """Scan for devices on remote SSH host."""
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "ConnectTimeout=10", ssh_host,
        f'{remote_adb} devices -l',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    output = stdout.decode(errors="replace")

    entries: list[DeviceEntry] = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        attrs = {}
        for part in parts[2:]:
            if ":" in part:
                k, v = part.split(":", 1)
                attrs[k] = v
        # SSH devices are USB-attached, store serial as label
        entries.append(DeviceEntry(
            ip=serial,
            port=0,  # not TCP/IP
            label=attrs.get("model", ""),
        ))

    return entries


@click.command("init")
def init() -> None:
    """Interactive setup wizard for CellSwarm."""
    console.print("\n[bold]CellSwarm Setup[/]")
    console.print("=" * 40)

    # 1. Connection mode
    console.print("\nConnection mode:")
    console.print("  [bold][1][/] Direct ADB (phones on network via adb connect)  [dim]<- recommended[/]")
    console.print("  [bold][2][/] SSH to remote ADB host (legacy WinPC setup)")
    mode_choice = click.prompt("\nSelect mode", type=click.IntRange(1, 2), default=1)
    mode = "direct" if mode_choice == 1 else "ssh"

    cfg = ConnectionConfig(mode=mode)

    if mode == "direct":
        # 2. ADB binary
        cfg.adb_bin = click.prompt("ADB binary path", default="adb")

        # 3. Auto-discover
        console.print("\n[bold]Scanning for devices...[/]")
        try:
            entries = _run(_discover_devices_direct(cfg.adb_bin))
        except Exception as e:
            console.print(f"[red]ADB scan failed: {e}[/]")
            entries = []

        if entries:
            console.print(f"  Found {len(entries)} TCP/IP device(s):")
            for e in entries:
                info = f"  [green]+[/] {e.ip}:{e.port}"
                if e.label:
                    info += f" ({e.label})"
                console.print(info)
            cfg.devices = entries
        else:
            console.print("  [yellow]No TCP/IP devices found.[/]")
            console.print("  Connect phones via: adb connect PHONE_IP:5555")

        # 4. Manual additions
        if click.confirm("\nAdd devices manually?", default=False):
            while True:
                addr = click.prompt("  Device IP[:PORT] (empty to stop)", default="")
                if not addr:
                    break
                if ":" in addr:
                    ip, port_str = addr.rsplit(":", 1)
                    try:
                        port = int(port_str)
                    except ValueError:
                        port = 5555
                else:
                    ip = addr
                    port = 5555
                label = click.prompt("  Label (optional)", default="")
                cfg.devices.append(DeviceEntry(ip=ip, port=port, label=label))
                console.print(f"  [green]+[/] Added {ip}:{port}")

        # 5. Host IP (auto-detect or manual)
        console.print()
        from cellswarm.transport.swarm_ring import _get_host_ip
        detected_ip = _get_host_ip()
        cfg.host_ip = click.prompt("Host IP (how phones reach this server)", default=detected_ip)

    else:
        # SSH mode setup
        cfg.ssh_host = click.prompt("SSH host", default="winpc")
        cfg.remote_adb_bin = click.prompt(
            "Remote ADB binary path",
            default=r'"C:\Program Files\platform-tools\adb.exe"',
        )
        cfg.staging_dir = click.prompt(
            "Windows staging directory",
            default=r"C:\Users\Lukio-4090\cellswarm-staging",
        )

        console.print("\n[bold]Scanning for devices on remote host...[/]")
        try:
            entries = _run(_discover_devices_ssh(cfg.ssh_host, cfg.remote_adb_bin))
        except Exception as e:
            console.print(f"[red]SSH scan failed: {e}[/]")
            entries = []

        if entries:
            console.print(f"  Found {len(entries)} device(s):")
            for e in entries:
                info = f"  [green]+[/] {e.ip}"
                if e.label:
                    info += f" ({e.label})"
                console.print(info)
            cfg.devices = entries
        else:
            console.print("  [yellow]No devices found.[/]")

    # 6. Write config
    ensure_dirs()
    try:
        save_config(cfg)
        console.print(f"\n[green]Saved config to {CONFIG_FILE}[/]")
    except Exception as e:
        console.print(f"\n[red]Failed to save config: {e}[/]")
        return

    reset_config()

    console.print(f"\nRun [bold]cellswarm devices list[/] to verify.")
    console.print()
