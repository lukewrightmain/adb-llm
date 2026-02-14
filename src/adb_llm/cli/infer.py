"""CLI command: adb-llm infer - one-command inference."""

from __future__ import annotations

import asyncio

import click

from adb_llm.core.config import DEFAULT_CONTEXT_SIZE, LLAMA_SERVER_HOST, LLAMA_SERVER_PORT


@click.command()
@click.argument("model_path", type=click.Path(exists=True))
@click.option("--port", default=LLAMA_SERVER_PORT, help="llama-server HTTP port")
@click.option("--host", default=LLAMA_SERVER_HOST, help="llama-server bind address")
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
    """Start distributed inference: rpc-servers + tunnels + llama-server.

    One command to go from zero to OpenAI-compatible API endpoint.
    """
    from adb_llm.inference.orchestrator import InferenceOrchestrator

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
