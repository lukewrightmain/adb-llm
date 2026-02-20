"""Parallel model distribution to devices via ADB push."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from cellswarm.core.config import REMOTE_MODELS_DIR
from cellswarm.core.device import DeviceInfo, DeviceState
from cellswarm.core.errors import InsufficientStorageError, ModelNotFoundError
from cellswarm.model.gguf_info import GGUFInfo, read_gguf_info
from cellswarm.model.registry import ModelRegistry
from cellswarm.utils.adb import adb_push, adb_shell


@dataclass
class PushResult:
    serial: str
    success: bool
    elapsed_seconds: float = 0.0
    speed_mbps: float = 0.0
    error: str = ""


class ModelDistributor:
    """Orchestrates parallel model pushes to devices."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self.console = Console()

    async def distribute(
        self,
        model_path: str | Path,
        devices: list[DeviceInfo],
        skip_existing: bool = True,
    ) -> list[PushResult]:
        """Push a model to all target devices in parallel with progress bars."""
        model_path = Path(model_path)
        if not model_path.exists():
            raise ModelNotFoundError(str(model_path))

        file_size = model_path.stat().st_size
        file_size_mb = file_size // (1024 * 1024)
        filename = model_path.name

        # Try to parse GGUF metadata
        gguf_info: GGUFInfo | None = None
        try:
            gguf_info = read_gguf_info(model_path)
            self.console.print(
                f"[bold]Model:[/] {gguf_info.display_name}  "
                f"[dim]({gguf_info.file_size_gb:.1f} GB, {gguf_info.tensor_count} tensors)[/]"
            )
        except Exception:
            self.console.print(f"[bold]File:[/] {filename} ({file_size_mb} MB)")

        # Check storage on each device
        targets: list[DeviceInfo] = []
        for dev in devices:
            if skip_existing and filename in dev.models:
                self.console.print(f"  [dim]{dev.serial}: already has {filename}, skipping[/]")
                continue
            if dev.storage_free_mb > 0 and dev.storage_free_mb < file_size_mb + 100:
                raise InsufficientStorageError(dev.serial, file_size_mb, dev.storage_free_mb)
            targets.append(dev)

        if not targets:
            self.console.print("[green]All devices already have this model.[/]")
            return []

        self.console.print(f"\nPushing to {len(targets)} device(s)...\n")

        # Create remote model directory on all devices first
        await asyncio.gather(*[
            adb_shell(dev.serial, f"mkdir -p {REMOTE_MODELS_DIR}", check=False)
            for dev in targets
        ])

        # Push in parallel with progress tracking
        results: list[PushResult] = []

        with Progress(
            TextColumn("[bold]{task.fields[serial]}"),
            BarColumn(bar_width=30),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=self.console,
        ) as progress:
            tasks: dict[str, TaskID] = {}
            for dev in targets:
                tasks[dev.serial] = progress.add_task(
                    dev.serial,
                    total=file_size,
                    serial=dev.short_serial,
                )

            async def _push_one(dev: DeviceInfo) -> PushResult:
                remote_path = f"{REMOTE_MODELS_DIR}/{filename}"
                try:
                    dev.state = DeviceState.BUSY
                    elapsed, speed = await adb_push(
                        dev.serial,
                        str(model_path),
                        remote_path,
                    )
                    # Mark complete in progress bar
                    progress.update(tasks[dev.serial], completed=file_size)
                    dev.state = DeviceState.READY
                    if filename not in dev.models:
                        dev.models.append(filename)
                    self.registry.register(filename, file_size, dev.serial)
                    return PushResult(
                        serial=dev.serial,
                        success=True,
                        elapsed_seconds=elapsed,
                        speed_mbps=speed / 1e6,
                    )
                except Exception as e:
                    dev.state = DeviceState.ERROR
                    logger.error("Push to {} failed: {}", dev.serial, e)
                    return PushResult(
                        serial=dev.serial,
                        success=False,
                        error=str(e),
                    )

            results = await asyncio.gather(*[_push_one(dev) for dev in targets])

        # Summary
        ok = sum(1 for r in results if r.success)
        fail = len(results) - ok
        if ok:
            avg_speed = sum(r.speed_mbps for r in results if r.success) / ok
            max_elapsed = max(r.elapsed_seconds for r in results if r.success)
            self.console.print(
                f"\n[green]{ok}/{len(results)} succeeded[/]  "
                f"avg {avg_speed:.0f} MB/s  wall time {max_elapsed:.1f}s"
            )
        if fail:
            self.console.print(f"[red]{fail} failed[/]")
            for r in results:
                if not r.success:
                    self.console.print(f"  [red]{r.serial}: {r.error}[/]")

        return list(results)
