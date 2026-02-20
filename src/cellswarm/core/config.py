"""Paths and default configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Directories
CELLSWARM_HOME = Path.home() / ".cellswarm"
MODELS_DIR = CELLSWARM_HOME / "models"
CONFIG_FILE = CELLSWARM_HOME / "config.yaml"
REGISTRY_FILE = CELLSWARM_HOME / "registry.json"

# Remote paths on Android devices
REMOTE_BASE = "/data/local/tmp/cellswarm"
REMOTE_MODELS_DIR = f"{REMOTE_BASE}/models"
REMOTE_BIN_DIR = f"{REMOTE_BASE}/bin"
REMOTE_SWARM_RPC = f"{REMOTE_BIN_DIR}/swarm-rpc"

# RPC defaults
RPC_REMOTE_PORT = 60000
RPC_BASE_LOCAL_PORT = 60001

# Timeouts (seconds)
ADB_COMMAND_TIMEOUT = 30
ADB_PUSH_TIMEOUT = 600  # 10 min for large models
PROBE_TIMEOUT = 10
RPC_START_TIMEOUT = 15
HEALTH_CHECK_INTERVAL = 5

# Inference server defaults
SWARM_SERVER_PORT = 8080
SWARM_SERVER_HOST = "127.0.0.1"
DEFAULT_CONTEXT_SIZE = 2048

# cellswarm paths
REMOTE_SWARM_WORKER = f"{REMOTE_BIN_DIR}/cellswarm-worker"

# cellswarm ZMQ ports
# Phones bind (listen) on these ports for ZMQ PULL sockets
SWARM_DATA_PORT = 9000         # cellswarm default data port (+ rank offset)
SWARM_SIGNAL_PORT = 10000      # cellswarm default signal port (+ rank offset)

# Tunnel port ranges (allocated per-phone, offset by phone index)
# adb forward: phone:bind_port -> winpc:FORWARD_BASE+idx (inbound to phone)
SWARM_FORWARD_BASE = 59001
# adb reverse: phone:REVERSE_BASE+idx -> winpc:port (outbound from phone)
SWARM_REVERSE_BASE = 58001
# SSH -R tunnels: winpc:port -> localhost:port (reverse, for outbound phone traffic)
SWARM_SSH_REVERSE_BASE = 58001
# SSH -L tunnels: localhost:port -> winpc:FORWARD_BASE+idx (forward, to reach phone binds)
SWARM_SSH_FORWARD_BASE = 59001

SWARM_START_TIMEOUT = 30  # Workers take longer to init (mmap model)


def ensure_dirs() -> None:
    """Create local directories if they don't exist."""
    CELLSWARM_HOME.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Connection configuration (onboarding)
# ---------------------------------------------------------------------------


@dataclass
class DeviceEntry:
    """A device listed in the config file."""
    ip: str
    port: int = 5555
    label: str = ""

    @property
    def serial(self) -> str:
        """ADB serial for TCP/IP devices: IP:PORT."""
        return f"{self.ip}:{self.port}"


@dataclass
class ConnectionConfig:
    """How CellSwarm reaches ADB devices.

    mode="direct": ADB runs locally, phones reachable via TCP/IP.
    mode="ssh":    ADB runs on a remote host via SSH (legacy WinPC setup).
    """
    mode: str = "direct"
    adb_bin: str = "adb"
    # SSH mode only
    ssh_host: str = "winpc"
    remote_adb_bin: str = r'"C:\Program Files\platform-tools\adb.exe"'
    staging_dir: str = r"C:\Users\Lukio-4090\cellswarm-staging"
    # Host IP for direct mode (how phones reach this server)
    host_ip: str = ""
    # Managed devices
    devices: list[DeviceEntry] = field(default_factory=list)


# Module-level singleton — loaded once, cached.
_config: ConnectionConfig | None = None


def load_config() -> ConnectionConfig:
    """Load connection config from ``~/.cellswarm/config.yaml``.

    Precedence: env vars → config file → defaults.
    Result is cached as a module-level singleton.
    """
    global _config
    if _config is not None:
        return _config

    cfg = ConnectionConfig()

    # Try loading YAML file
    if CONFIG_FILE.exists():
        try:
            import yaml  # type: ignore[import-untyped]
        except ModuleNotFoundError:
            # pyyaml not installed — fall back to defaults
            pass
        else:
            with open(CONFIG_FILE) as f:
                raw = yaml.safe_load(f) or {}

            conn = raw.get("connection", {})
            if isinstance(conn, dict):
                cfg.mode = conn.get("mode", cfg.mode)
                cfg.adb_bin = conn.get("adb_bin", cfg.adb_bin)
                cfg.ssh_host = conn.get("ssh_host", cfg.ssh_host)
                cfg.remote_adb_bin = conn.get("remote_adb_bin", cfg.remote_adb_bin)
                cfg.staging_dir = conn.get("staging_dir", cfg.staging_dir)
                cfg.host_ip = conn.get("host_ip", cfg.host_ip)

            devs_raw = raw.get("devices", [])
            if isinstance(devs_raw, list):
                for d in devs_raw:
                    if isinstance(d, dict) and "ip" in d:
                        cfg.devices.append(DeviceEntry(
                            ip=d["ip"],
                            port=int(d.get("port", 5555)),
                            label=d.get("label", ""),
                        ))

    # Env var overrides (highest precedence)
    cfg.mode = os.environ.get("CELLSWARM_MODE", cfg.mode)
    cfg.adb_bin = os.environ.get("CELLSWARM_ADB_BIN_LOCAL", cfg.adb_bin)
    cfg.ssh_host = os.environ.get("CELLSWARM_SSH_HOST", cfg.ssh_host)
    cfg.remote_adb_bin = os.environ.get("CELLSWARM_ADB_BIN", cfg.remote_adb_bin)
    cfg.staging_dir = os.environ.get("CELLSWARM_WIN_STAGING", cfg.staging_dir)
    cfg.host_ip = os.environ.get("CELLSWARM_HOST_IP", cfg.host_ip)

    _config = cfg
    return cfg


def save_config(cfg: ConnectionConfig) -> None:
    """Write connection config to ``~/.cellswarm/config.yaml``."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ModuleNotFoundError:
        raise RuntimeError("pyyaml is required to save config: pip install pyyaml")

    ensure_dirs()

    data: dict = {
        "connection": {
            "mode": cfg.mode,
            "adb_bin": cfg.adb_bin,
        },
        "devices": [],
    }

    # Only include SSH fields when mode is ssh
    if cfg.mode == "ssh":
        data["connection"]["ssh_host"] = cfg.ssh_host
        data["connection"]["remote_adb_bin"] = cfg.remote_adb_bin
        data["connection"]["staging_dir"] = cfg.staging_dir

    if cfg.host_ip:
        data["connection"]["host_ip"] = cfg.host_ip

    for dev in cfg.devices:
        entry: dict = {"ip": dev.ip, "port": dev.port}
        if dev.label:
            entry["label"] = dev.label
        data["devices"].append(entry)

    with open(CONFIG_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def reset_config() -> None:
    """Clear cached config so next ``load_config()`` re-reads from disk."""
    global _config
    _config = None
