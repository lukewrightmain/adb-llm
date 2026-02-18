"""CLI commands: adb-llm prima — pipeline-ring inference via prima.cpp."""

from __future__ import annotations

import asyncio

import click
from rich.console import Console
from rich.table import Table

from adb_llm.core.config import DEFAULT_CONTEXT_SIZE, LLAMA_SERVER_HOST, LLAMA_SERVER_PORT


@click.group()
def prima() -> None:
    """Prima.cpp pipeline-ring parallelism (faster than RPC tensor parallelism)."""


@prima.command()
@click.argument("model_path", type=click.Path(exists=True))
@click.option("--port", default=LLAMA_SERVER_PORT, help="API port")
@click.option("--host", default=LLAMA_SERVER_HOST, help="Bind address")
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
    """Start prima.cpp pipeline inference.

    Sets up ring topology, starts workers on phones, launches host rank 0.
    Model must already be on all target devices (use `adb-llm distribute` first).
    """
    from adb_llm.inference.prima_orchestrator import PrimaOrchestrator

    orchestrator = PrimaOrchestrator()
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


@prima.command()
def stop() -> None:
    """Stop all prima-workers and tear down ring."""
    from adb_llm.core.device_manager import DeviceManager
    from adb_llm.inference.prima_manager import PrimaManager
    from adb_llm.utils.adb import adb_reverse_remove_all

    console = Console()

    async def _stop() -> None:
        console.print("[bold]Stopping prima.cpp pipeline...[/]")

        dm = DeviceManager()
        await dm.discover()
        devices = dm.all_devices

        pm = PrimaManager()
        for dev in devices:
            if await pm.is_running(dev):
                await pm.stop(dev)
                console.print(f"  Stopped prima-worker on {dev.serial[:8]}")
            # Clean up reverse tunnels
            await adb_reverse_remove_all(dev.serial)

        # Kill any local prima-host process
        import subprocess
        subprocess.run(["pkill", "-f", "prima-host"], capture_output=True)

        console.print("[green]Prima.cpp stopped.[/]")

    asyncio.get_event_loop().run_until_complete(_stop())


@prima.command()
def status() -> None:
    """Show ring topology and worker status."""
    from adb_llm.core.device_manager import DeviceManager
    from adb_llm.inference.prima_manager import PrimaManager

    console = Console()

    async def _status() -> None:
        dm = DeviceManager()
        await dm.discover()
        await dm.probe_all()
        devices = dm.all_devices

        pm = PrimaManager()

        table = Table(title="Prima.cpp Worker Status")
        table.add_column("Serial", style="cyan")
        table.add_column("Model", style="white")
        table.add_column("RAM (MB)", style="yellow")
        table.add_column("prima-worker", style="green")

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
            ["pgrep", "-f", "prima-host"], capture_output=True, text=True,
        )
        if result.stdout.strip():
            console.print(f"\n[green]prima-host running[/] (PID {result.stdout.strip()})")
        else:
            console.print("\n[dim]prima-host not running[/]")

    asyncio.get_event_loop().run_until_complete(_status())


@prima.command()
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
    """Benchmark prima.cpp pipeline inference and compare to RPC baseline."""
    import time

    from adb_llm.inference.prima_orchestrator import PrimaOrchestrator

    console = Console()

    async def _bench() -> None:
        orchestrator = PrimaOrchestrator()

        console.print("[bold]Starting prima.cpp benchmark...[/]\n")

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
