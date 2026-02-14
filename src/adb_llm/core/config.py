"""Paths and default configuration."""

from pathlib import Path

# Directories
ADB_LLM_HOME = Path.home() / ".adb-llm"
MODELS_DIR = ADB_LLM_HOME / "models"
CONFIG_FILE = ADB_LLM_HOME / "config.yaml"
REGISTRY_FILE = ADB_LLM_HOME / "registry.json"

# Remote paths on Android devices
REMOTE_BASE = "/data/local/tmp/adb-llm"
REMOTE_MODELS_DIR = f"{REMOTE_BASE}/models"
REMOTE_BIN_DIR = f"{REMOTE_BASE}/bin"
REMOTE_RPC_SERVER = f"{REMOTE_BIN_DIR}/rpc-server"

# RPC defaults
RPC_REMOTE_PORT = 60000
RPC_BASE_LOCAL_PORT = 60001

# Timeouts (seconds)
ADB_COMMAND_TIMEOUT = 30
ADB_PUSH_TIMEOUT = 600  # 10 min for large models
PROBE_TIMEOUT = 10
RPC_START_TIMEOUT = 15
HEALTH_CHECK_INTERVAL = 5

# llama-server defaults
LLAMA_SERVER_PORT = 8080
LLAMA_SERVER_HOST = "127.0.0.1"
DEFAULT_CONTEXT_SIZE = 2048


def ensure_dirs() -> None:
    """Create local directories if they don't exist."""
    ADB_LLM_HOME.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
