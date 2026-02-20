"""CLI entry point for cellswarm."""

import click
from loguru import logger

from cellswarm.cli.devices import devices
from cellswarm.cli.distribute import distribute
from cellswarm.cli.init import init
from cellswarm.cli.tunnel import tunnel
from cellswarm.cli.infer import infer
from cellswarm.cli.swarm import swarm


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """CellSwarm — Distributed LLM inference on Android phone clusters."""
    import sys

    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, format="<level>{level: <8}</level> | {message}")


@cli.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=8000, type=int, help="Port number")
@click.option("--reload", is_flag=True, help="Enable auto-reload (dev)")
def serve(host: str, port: int, reload: bool) -> None:
    """Launch the dashboard web server."""
    import uvicorn

    uvicorn.run(
        "cellswarm.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


cli.add_command(init)
cli.add_command(devices)
cli.add_command(distribute)
cli.add_command(tunnel)
cli.add_command(infer)
cli.add_command(swarm)
