"""Global application state singleton."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from loguru import logger

from cellswarm.core.device_manager import DeviceManager
from cellswarm.inference.swarm_orchestrator import SwarmOrchestrator


@dataclass
class JobInfo:
    job_id: str
    status: str = "pending"  # pending, running, completed, failed
    progress: float = 0.0
    message: str = ""


class AppState:
    """Holds shared state for the dashboard server."""

    def __init__(self) -> None:
        self.device_manager = DeviceManager()
        self.orchestrator = SwarmOrchestrator()
        # Share the same device_manager
        self.orchestrator.device_manager = self.device_manager

        self.download_jobs: dict[str, JobInfo] = {}
        self.distribute_jobs: dict[str, JobInfo] = {}

        self._monitor_task: asyncio.Task | None = None
        self._active_model: str | None = None
        self._active_draft_model: str | None = None

        # Ring launch tracking
        self._launch_task: asyncio.Task | None = None
        self._launch_status: str = "idle"  # idle, running, done, failed
        self._launch_message: str = ""
        self._launch_error: str | None = None

    async def startup(self) -> None:
        """Initial device discovery on server start."""
        logger.info("Dashboard startup: discovering devices...")
        try:
            await self.device_manager.discover()
            await self.device_manager.probe_all()
            ready = await self.device_manager.get_ready_devices()
            logger.info("Found {} ready devices", len(ready))
        except Exception as e:
            logger.error("Initial discovery failed: {}", e)

        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def shutdown(self) -> None:
        """Cleanup on server stop."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self) -> None:
        """Background loop: re-probe devices every 10s."""
        while True:
            await asyncio.sleep(10)
            try:
                await self.device_manager.probe_all()
            except Exception as e:
                logger.warning("Monitor probe failed: {}", e)

    async def refresh_devices(self) -> None:
        """Force full re-discovery + probe."""
        await self.device_manager.discover()
        await self.device_manager.probe_all()

    def new_job_id(self) -> str:
        return str(uuid.uuid4())[:8]


# Module-level singleton
app_state = AppState()
