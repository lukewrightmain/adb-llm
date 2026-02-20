"""Track which models are on which devices."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cellswarm.core.config import REGISTRY_FILE, REMOTE_MODELS_DIR, ensure_dirs


@dataclass
class ModelEntry:
    """A model file tracked on a device."""
    filename: str
    size_bytes: int
    remote_path: str
    devices: list[str] = field(default_factory=list)  # serials


class ModelRegistry:
    """Persistent registry of models distributed to devices."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or REGISTRY_FILE
        self.models: dict[str, ModelEntry] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            for name, entry in data.items():
                self.models[name] = ModelEntry(**entry)

    def save(self) -> None:
        ensure_dirs()
        data = {}
        for name, entry in self.models.items():
            data[name] = {
                "filename": entry.filename,
                "size_bytes": entry.size_bytes,
                "remote_path": entry.remote_path,
                "devices": entry.devices,
            }
        self.path.write_text(json.dumps(data, indent=2))

    def register(self, filename: str, size_bytes: int, serial: str) -> None:
        """Record that a model was pushed to a device."""
        if filename not in self.models:
            self.models[filename] = ModelEntry(
                filename=filename,
                size_bytes=size_bytes,
                remote_path=f"{REMOTE_MODELS_DIR}/{filename}",
                devices=[],
            )
        entry = self.models[filename]
        if serial not in entry.devices:
            entry.devices.append(serial)
        self.save()

    def get_devices_with_model(self, filename: str) -> list[str]:
        """Return serials that have a given model."""
        entry = self.models.get(filename)
        return entry.devices if entry else []

    def get_models_on_device(self, serial: str) -> list[ModelEntry]:
        """Return all models on a given device."""
        return [e for e in self.models.values() if serial in e.devices]

    def get_remote_path(self, filename: str) -> str:
        """Get the remote path for a model file."""
        entry = self.models.get(filename)
        if entry:
            return entry.remote_path
        return f"{REMOTE_MODELS_DIR}/{filename}"
