"""Pydantic models for API request/response shapes."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel


class DeviceStateAPI(str, Enum):
    connected = "connected"
    ready = "ready"
    busy = "busy"
    error = "error"
    offline = "offline"


class DeviceResponse(BaseModel):
    serial: str
    short_serial: str
    state: DeviceStateAPI
    model: str
    total_ram_mb: int
    available_ram_mb: int
    usable_ram_mb: int
    storage_free_mb: int
    cpu_cores: int
    cpu_arch: str
    android_version: str
    thermal_temp_c: float
    has_swarm_worker: bool
    models: list[str]


class DevicesResponse(BaseModel):
    devices: list[DeviceResponse]
    count: int
    ready_count: int


class RingNodeResponse(BaseModel):
    rank: int
    serial: str
    is_host: bool
    data_port: int
    running: bool
    layers: int | None = None
    model: str | None = None
    thermal_temp_c: float | None = None
    available_ram_mb: int | None = None


class RingStatusResponse(BaseModel):
    active: bool
    world_size: int
    model: str | None = None
    nodes: list[RingNodeResponse]


class RingStartRequest(BaseModel):
    model_path: str
    devices: str = "all"
    total_layers: int = 64
    context_size: int = 2048
    prefetch: bool = True
    port: int = 8080


class ModelFileResponse(BaseModel):
    name: str
    size_mb: int
    path: str


class ModelsResponse(BaseModel):
    local_models: list[ModelFileResponse]
    device_models: dict[str, list[str]]


class DownloadRequest(BaseModel):
    repo_id: str | None = None
    filename: str | None = None
    url: str | None = None


class DistributeRequest(BaseModel):
    model_name: str
    devices: str = "all"


class JobResponse(BaseModel):
    job_id: str
    status: str


class JobProgressResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    message: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "default"
    messages: list[ChatMessage]
    stream: bool = True
    temperature: float = 0.7
    max_tokens: int = 2048
