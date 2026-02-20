"""Model management routes — list, download, distribute."""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException

from cellswarm.core.config import MODELS_DIR, REMOTE_MODELS_DIR
from cellswarm.server.schemas import (
    DistributeRequest,
    DownloadRequest,
    JobProgressResponse,
    JobResponse,
    ModelFileResponse,
    ModelsResponse,
)
from cellswarm.server.state import JobInfo, app_state

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=ModelsResponse)
async def list_models():
    # Local GGUF files
    local: list[ModelFileResponse] = []
    models_dir = str(MODELS_DIR)
    if os.path.isdir(models_dir):
        for f in os.listdir(models_dir):
            if f.endswith(".gguf"):
                path = os.path.join(models_dir, f)
                size_mb = os.path.getsize(path) // (1024 * 1024)
                local.append(ModelFileResponse(name=f, size_mb=size_mb, path=path))

    # Per-device models
    device_models: dict[str, list[str]] = {}
    for serial, dev in app_state.device_manager.devices.items():
        device_models[dev.short_serial] = dev.models

    return ModelsResponse(local_models=local, device_models=device_models)


@router.post("/download", response_model=JobResponse)
async def download_model(req: DownloadRequest):
    job_id = app_state.new_job_id()
    job = JobInfo(job_id=job_id, status="running", message="Starting download...")
    app_state.download_jobs[job_id] = job

    asyncio.create_task(_do_download(job, req))
    return JobResponse(job_id=job_id, status="running")


async def _do_download(job: JobInfo, req: DownloadRequest) -> None:
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        if req.repo_id:
            from huggingface_hub import hf_hub_download

            job.message = f"Downloading from {req.repo_id}..."
            path = await asyncio.to_thread(
                hf_hub_download,
                repo_id=req.repo_id,
                filename=req.filename or "*.gguf",
                local_dir=str(MODELS_DIR),
            )
            job.progress = 1.0
            job.status = "completed"
            job.message = f"Downloaded to {path}"

        elif req.url:
            import httpx

            job.message = f"Downloading from URL..."
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(600.0)) as client:
                async with client.stream("GET", req.url) as resp:
                    filename = req.url.split("/")[-1]
                    dest = MODELS_DIR / filename
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0

                    with open(dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                job.progress = downloaded / total
                            job.message = f"Downloaded {downloaded // (1024*1024)}MB"

            job.progress = 1.0
            job.status = "completed"
            job.message = f"Downloaded {filename}"
        else:
            job.status = "failed"
            job.message = "Provide repo_id or url"

    except Exception as e:
        job.status = "failed"
        job.message = str(e)


@router.get("/downloads", response_model=list[JobProgressResponse])
async def download_progress():
    return [
        JobProgressResponse(
            job_id=j.job_id, status=j.status, progress=j.progress, message=j.message
        )
        for j in app_state.download_jobs.values()
    ]


@router.post("/distribute", response_model=JobResponse)
async def distribute_model(req: DistributeRequest):
    job_id = app_state.new_job_id()
    job = JobInfo(job_id=job_id, status="running", message="Starting distribution...")
    app_state.distribute_jobs[job_id] = job

    asyncio.create_task(_do_distribute(job, req))
    return JobResponse(job_id=job_id, status="running")


async def _do_distribute(job: JobInfo, req: DistributeRequest) -> None:
    try:
        from cellswarm.utils.adb import adb_shell

        model_path = MODELS_DIR / req.model_name
        if not model_path.exists():
            job.status = "failed"
            job.message = f"Model {req.model_name} not found locally"
            return

        devices = app_state.device_manager.resolve_targets(req.devices)
        if not devices:
            job.status = "failed"
            job.message = "No devices available"
            return

        remote_path = f"{REMOTE_MODELS_DIR}/{req.model_name}"

        for i, dev in enumerate(devices):
            job.message = f"Pushing to {dev.short_serial} ({i+1}/{len(devices)})"
            job.progress = i / len(devices)

            # Check if already exists
            out = await adb_shell(dev.serial, f"ls -la {remote_path}", check=False)
            if "No such file" not in out and out.strip():
                continue

            from cellswarm.utils.adb import adb_push
            await adb_push(dev.serial, str(model_path), remote_path)

        job.progress = 1.0
        job.status = "completed"
        job.message = f"Distributed to {len(devices)} devices"

    except Exception as e:
        job.status = "failed"
        job.message = str(e)


@router.get("/distributions", response_model=list[JobProgressResponse])
async def distribution_progress():
    return [
        JobProgressResponse(
            job_id=j.job_id, status=j.status, progress=j.progress, message=j.message
        )
        for j in app_state.distribute_jobs.values()
    ]
