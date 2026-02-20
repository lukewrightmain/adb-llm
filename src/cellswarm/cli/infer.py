"""CLI command: cellswarm infer - one-command inference."""

from __future__ import annotations

import asyncio

import click

from cellswarm.core.config import DEFAULT_CONTEXT_SIZE, SWARM_SERVER_HOST, SWARM_SERVER_PORT


@click.command()
@click.argument("model_path", type=click.Path(exists=True))
@click.option("--port", default=SWARM_SERVER_PORT, help="swarm-server HTTP port")
@click.option("--host", default=SWARM_SERVER_HOST, help="swarm-server bind address")
@click.option("--context-size", "-c", default=DEFAULT_CONTEXT_SIZE, help="Context size")
@click.option("--tensor-split", default=None, help="Custom tensor split (auto if omitted)")
@click.option("--devices", "target", default="all", help="Target devices: 'all' or serials")
def infer(
    model_path: str,
    port: int,
    host: str,
    context_size: int,
    tensor_split: str | None,
    target: str,
) -> None:
    """Start distributed inference: swarm-rpcs + tunnels + swarm-server.

    One command to go from zero to OpenAI-compatible API endpoint.
    """
    from cellswarm.inference.orchestrator import InferenceOrchestrator

    orchestrator = InferenceOrchestrator()
    asyncio.get_event_loop().run_until_complete(
        orchestrator.run(
            model_path=model_path,
            port=port,
            host=host,
            context_size=context_size,
            tensor_split=tensor_split,
            target_devices=target,
        )
    )
