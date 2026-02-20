"""CLI command: cellswarm distribute <model_path>."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from rich.console import Console

from cellswarm.core.device_manager import DeviceManager
from cellswarm.model.distributor import ModelDistributor

console = Console()


@click.command()
@click.argument("model_path", type=click.Path(exists=True))
@click.option(
    "--devices", "target",
    default="all",
    help="Target devices: 'all' or comma-separated serials",
)
@click.option("--force", is_flag=True, help="Push even if model already exists on device")
def distribute(model_path: str, target: str, force: bool) -> None:
    """Push a model file to devices in parallel via ADB."""
    asyncio.get_event_loop().run_until_complete(
        _distribute(model_path, target, force)
    )


async def _distribute(model_path: str, target: str, force: bool) -> None:
    dm = DeviceManager()
    await dm.discover()
    await dm.probe_all()

    devices = dm.resolve_targets(target)
    if not devices:
        console.print("[yellow]No matching devices found.[/]")
        return

    distributor = ModelDistributor()
    await distributor.distribute(
        model_path,
        devices,
        skip_existing=not force,
    )
