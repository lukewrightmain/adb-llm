"""Paths and default configuration."""

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
