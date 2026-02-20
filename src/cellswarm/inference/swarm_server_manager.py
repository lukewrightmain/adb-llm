"""Manage swarm-server process on the host PC."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal

from loguru import logger

from cellswarm.core.config import DEFAULT_CONTEXT_SIZE, SWARM_SERVER_HOST, SWARM_SERVER_PORT
from cellswarm.core.errors import InferenceError


class SwarmServerManager:
    """Start/stop swarm-server on the host machine."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._swarm_server_bin = shutil.which("swarm-server") or "swarm-server"

    async def start(
        self,
        model_path: str,
        rpc_endpoints: str,
        tensor_split: str | None = None,
        port: int = SWARM_SERVER_PORT,
        host: str = SWARM_SERVER_HOST,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        extra_args: list[str] | None = None,
    ) -> None:
        """Start swarm-server with RPC backends pointing at USB tunnels."""
        if self._process and self._process.returncode is None:
            logger.warning("swarm-server already running, stopping first")
            await self.stop()

        args = [
            self._swarm_server_bin,
            "-m", model_path,
            "--rpc", rpc_endpoints,
            "--port", str(port),
            "--host", host,
            "-c", str(context_size),
        ]

        if tensor_split:
            args.extend(["--tensor-split", tensor_split])

        if extra_args:
            args.extend(extra_args)

        logger.info("Starting swarm-server: {}", " ".join(args))

        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GGML_RPC_COMPRESS": "1"},
        )

        # Wait briefly to check it didn't immediately crash
        try:
            await asyncio.wait_for(self._process.wait(), timeout=3)
            # If we get here, it exited too quickly
            stderr = (await self._process.stderr.read()).decode(errors="replace")
            raise InferenceError(f"swarm-server exited immediately:\n{stderr[:1000]}")
        except asyncio.TimeoutError:
            # Good - still running after 3s
            pass

        logger.info("swarm-server started on {}:{} (PID {})", host, port, self._process.pid)

    async def stop(self) -> None:
        """Stop swarm-server."""
        if self._process and self._process.returncode is None:
            logger.info("Stopping swarm-server (PID {})", self._process.pid)
            self._process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            self._process = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def wait(self) -> int:
        """Wait for swarm-server to exit, return exit code."""
        if not self._process:
            return -1
        return await self._process.wait()

    async def stream_logs(self) -> None:
        """Stream swarm-server stderr to loguru."""
        if not self._process or not self._process.stderr:
            return
        async for line in self._process.stderr:
            text = line.decode(errors="replace").rstrip()
            if text:
                logger.info("[swarm-server] {}", text)
