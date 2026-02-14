"""CLI entry point for adb-llm."""

import click
from loguru import logger

from adb_llm.cli.devices import devices
from adb_llm.cli.distribute import distribute
from adb_llm.cli.tunnel import tunnel
from adb_llm.cli.infer import infer


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """adb-llm: Distributed LLM inference via ADB/USB."""
    import sys

    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, format="<level>{level: <8}</level> | {message}")


cli.add_command(devices)
cli.add_command(distribute)
cli.add_command(tunnel)
cli.add_command(infer)
