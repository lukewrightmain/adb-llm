"""CLI commands: cellswarm swarm — pipeline-ring inference."""

from __future__ import annotations

import asyncio

import click
from rich.console import Console
from rich.table import Table

from cellswarm.core.config import DEFAULT_CONTEXT_SIZE, SWARM_SERVER_HOST, SWARM_SERVER_PORT


@click.group()
def swarm() -> None:
    """CellSwarm pipeline-ring parallelism (faster than RPC tensor parallelism)."""


@swarm.command()
@click.argument("model_path", type=click.Path(exists=True))
@click.option("--port", default=SWARM_SERVER_PORT, help="API port")
@click.option("--host", default=SWARM_SERVER_HOST, help="Bind address")
@click.option("--context-size", "-c", default=DEFAULT_CONTEXT_SIZE, help="Context size")
@click.option("--devices", "target", default="all", help="Target devices: 'all' or serials")
@click.option("--layers", default=64, help="Total transformer layers in model")
@click.option("--no-prefetch", is_flag=True, help="Disable weight prefetching")
def start(
    model_path: str,
    port: int,
    host: str,
    context_size: int,
    target: str,
    layers: int,
    no_prefetch: bool,
) -> None:
    """Start cellswarm pipeline inference.

    Sets up ring topology, starts workers on phones, launches host rank 0.
    Model must already be on all target devices (use `cellswarm distribute` first).
    """
    from cellswarm.inference.swarm_orchestrator import SwarmOrchestrator

    orchestrator = SwarmOrchestrator()
    asyncio.get_event_loop().run_until_complete(
        orchestrator.run(
            model_path=model_path,
            port=port,
            host=host,
            context_size=context_size,
            target_devices=target,
            total_layers=layers,
            prefetch=not no_prefetch,
        )
    )


@swarm.command()
def stop() -> None:
    """Stop all cellswarm-workers and tear down ring."""
    from cellswarm.core.device_manager import DeviceManager
    from cellswarm.inference.swarm_manager import SwarmManager
    from cellswarm.utils.adb import adb_reverse_remove_all

    console = Console()

    async def _stop() -> None:
        console.print("[bold]Stopping cellswarm pipeline...[/]")

        dm = DeviceManager()
        await dm.discover()
        devices = dm.all_devices

        pm = SwarmManager()
        for dev in devices:
            if await pm.is_running(dev):
                await pm.stop(dev)
                console.print(f"  Stopped cellswarm-worker on {dev.serial[:8]}")
            # Clean up reverse tunnels
            await adb_reverse_remove_all(dev.serial)

        # Kill any local cellswarm-host process
        import subprocess
        subprocess.run(["pkill", "-f", "cellswarm-host"], capture_output=True)

        console.print("[green]CellSwarm stopped.[/]")

    asyncio.get_event_loop().run_until_complete(_stop())


@swarm.command()
def status() -> None:
    """Show ring topology and worker status."""
    from cellswarm.core.device_manager import DeviceManager
    from cellswarm.inference.swarm_manager import SwarmManager

    console = Console()

    async def _status() -> None:
        dm = DeviceManager()
        await dm.discover()
        await dm.probe_all()
        devices = dm.all_devices

        pm = SwarmManager()

        table = Table(title="CellSwarm Worker Status")
        table.add_column("Serial", style="cyan")
        table.add_column("Model", style="white")
        table.add_column("RAM (MB)", style="yellow")
        table.add_column("cellswarm-worker", style="green")

        for dev in devices:
            running = await pm.is_running(dev)
            status_str = "[green]running[/]" if running else "[dim]stopped[/]"
            table.add_row(
                dev.serial[:12],
                dev.model,
                str(dev.available_ram_mb),
                status_str,
            )

        console.print(table)

        # Check host process
        import subprocess
        result = subprocess.run(
            ["pgrep", "-f", "cellswarm-host"], capture_output=True, text=True,
        )
        if result.stdout.strip():
            console.print(f"\n[green]cellswarm-host running[/] (PID {result.stdout.strip()})")
        else:
            console.print("\n[dim]cellswarm-host not running[/]")

    asyncio.get_event_loop().run_until_complete(_status())


@swarm.command()
@click.argument("model_path", type=click.Path(exists=True))
@click.option("--devices", "target", default="all", help="Target devices")
@click.option("--context-size", "-c", default=DEFAULT_CONTEXT_SIZE, help="Context size")
@click.option("--layers", default=64, help="Total transformer layers")
@click.option("--n-predict", default=128, help="Tokens to generate for benchmark")
def benchmark(
    model_path: str,
    target: str,
    context_size: int,
    layers: int,
    n_predict: int,
) -> None:
    """Benchmark cellswarm pipeline inference and compare to RPC baseline."""
    import time

    from cellswarm.inference.swarm_orchestrator import SwarmOrchestrator

    console = Console()

    async def _bench() -> None:
        orchestrator = SwarmOrchestrator()

        console.print("[bold]Starting cellswarm benchmark...[/]\n")

        try:
            await orchestrator.start(
                model_path=model_path,
                context_size=context_size,
                target_devices=target,
                total_layers=layers,
            )
        except Exception as e:
            console.print(f"[red]Failed to start: {e}[/]")
            return

        # Give it a moment to fully initialize
        await asyncio.sleep(5)

        # Run benchmark via HTTP
        import json
        import urllib.request

        prompt = "Write a short poem about distributed computing on smartphones."
        payload = json.dumps({
            "prompt": prompt,
            "n_predict": n_predict,
            "temperature": 0,
        }).encode()

        console.print(f"[bold]Generating {n_predict} tokens...[/]")
        t0 = time.monotonic()

        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8080/completion",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode())
        except Exception as e:
            console.print(f"[red]Benchmark request failed: {e}[/]")
            await orchestrator.shutdown()
            return

        elapsed = time.monotonic() - t0

        # Parse timing from response
        tokens_predicted = result.get("tokens_predicted", n_predict)
        tok_per_sec = tokens_predicted / elapsed if elapsed > 0 else 0

        console.print(f"\n[bold green]Benchmark Results:[/]")
        console.print(f"  Tokens generated: {tokens_predicted}")
        console.print(f"  Total time: {elapsed:.1f}s")
        console.print(f"  Speed: [bold]{tok_per_sec:.2f} tok/s[/]")
        console.print(f"\n  RPC baseline: ~1.07 tok/s")
        console.print(f"  Speedup: [bold]{tok_per_sec / 1.07:.1f}x[/]")

        await orchestrator.shutdown()

    asyncio.get_event_loop().run_until_complete(_bench())
