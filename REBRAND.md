# CellSwarm Rebrand Changelog

Full rebrand from `adb-llm` / `prima.cpp` to **CellSwarm**. Branch: `production-rebrand`.

## Project Identity

| Old | New |
|-----|-----|
| `adb-llm` | `cellswarm` |
| `adb_llm` (Python package) | `cellswarm` |
| `prima.cpp` / `Prima.cpp` (branding) | `CellSwarm` |
| `prima-dashboard` (npm package) | `cellswarm-dashboard` |

## CLI

| Old | New |
|-----|-----|
| `adb-llm` (command) | `cellswarm` |
| `adb-llm prima start\|stop\|status\|benchmark` | `cellswarm swarm start\|stop\|status\|benchmark` |
| `adb-llm infer` | `cellswarm infer` |
| `adb-llm devices` | `cellswarm devices` |
| `adb-llm distribute` | `cellswarm distribute` |
| `adb-llm serve` | `cellswarm serve` |

## Binaries

| Old | New |
|-----|-----|
| `prima-worker` (ARM64 phone binary) | `cellswarm-worker` |
| `prima-worker-spec` (ARM64 speculative) | `cellswarm-worker-spec` |
| `prima-host` (x86_64 host binary) | `cellswarm-host` |
| `prima-host-spec` (x86_64 speculative) | `cellswarm-host-spec` |
| `rpc-server` (ARM64 RPC binary) | `swarm-rpc` |
| `llama-server` (host inference server) | `swarm-server` |

## Python Classes

| Old | New | File |
|-----|-----|------|
| `PrimaManager` | `SwarmManager` | `inference/swarm_manager.py` |
| `PrimaOrchestrator` | `SwarmOrchestrator` | `inference/swarm_orchestrator.py` |
| `PrimaRingTransport` | `SwarmRingTransport` | `transport/swarm_ring.py` |
| `RpcManager` | `SwarmRpcManager` | `inference/swarm_rpc_manager.py` |
| `ServerManager` | `SwarmServerManager` | `inference/swarm_server_manager.py` |

## Python Files Renamed

| Old | New |
|-----|-----|
| `src/adb_llm/` | `src/cellswarm/` |
| `cli/prima.py` | `cli/swarm.py` |
| `inference/prima_manager.py` | `inference/swarm_manager.py` |
| `inference/prima_orchestrator.py` | `inference/swarm_orchestrator.py` |
| `inference/rpc_manager.py` | `inference/swarm_rpc_manager.py` |
| `inference/server_manager.py` | `inference/swarm_server_manager.py` |
| `transport/prima_ring.py` | `transport/swarm_ring.py` |

## Configuration Constants

| Old | New |
|-----|-----|
| `ADB_LLM_HOME` | `CELLSWARM_HOME` |
| `LLAMA_SERVER_PORT` | `SWARM_SERVER_PORT` |
| `LLAMA_SERVER_HOST` | `SWARM_SERVER_HOST` |
| `REMOTE_RPC_SERVER` | `REMOTE_SWARM_RPC` |
| `REMOTE_PRIMA_WORKER` | `REMOTE_SWARM_WORKER` |
| `PRIMA_DATA_PORT` | `SWARM_DATA_PORT` |
| `PRIMA_SIGNAL_PORT` | `SWARM_SIGNAL_PORT` |
| `PRIMA_FORWARD_BASE` | `SWARM_FORWARD_BASE` |
| `PRIMA_REVERSE_BASE` | `SWARM_REVERSE_BASE` |
| `PRIMA_SSH_FORWARD_BASE` | `SWARM_SSH_FORWARD_BASE` |
| `PRIMA_SSH_REVERSE_BASE` | `SWARM_SSH_REVERSE_BASE` |
| `PRIMA_START_TIMEOUT` | `SWARM_START_TIMEOUT` |
| `PRIMA_HOST_URL` | `SWARM_HOST_URL` |
| `PRIMA_DECODE_PROFILE` | `SWARM_DECODE_PROFILE` |
| `PRIMA_BATCH_PIPELINE` | `SWARM_BATCH_PIPELINE` |
| `PRIMA_NO_INTERLEAVE` | `SWARM_NO_INTERLEAVE` |
| `PRIMA_DEBUG` (C define) | `SWARM_DEBUG` |
| `LLAMA_CACHE` | `SWARM_CACHE` |

## Environment Variables

| Old | New |
|-----|-----|
| `ADB_LLM_SSH_HOST` | `CELLSWARM_SSH_HOST` |
| `ADB_LLM_ADB_BIN` | `CELLSWARM_ADB_BIN` |
| `ADB_LLM_WIN_STAGING` | `CELLSWARM_WIN_STAGING` |

## Filesystem Paths

| Old | New |
|-----|-----|
| `~/.adb-llm/` | `~/.cellswarm/` |
| `/data/local/tmp/adb-llm/` | `/data/local/tmp/cellswarm/` |
| `C:\Users\...\adb-llm-staging\` | `C:\Users\...\cellswarm-staging\` |
| `/tmp/ssh-prima-*` | `/tmp/ssh-cellswarm-*` |
| `prima-worker.log` | `cellswarm-worker.log` |
| `rpc-server.log` | `swarm-rpc.log` |

## Schema / API Fields

| Old | New |
|-----|-----|
| `has_prima_worker` | `has_swarm_worker` |
| `has_rpc_server` | `has_swarm_rpc` |
| `rpc_server_path` | `swarm_rpc_path` |
| `rpc_server_running` | `swarm_rpc_running` |

## Dashboard / Frontend

| Old | New |
|-----|-----|
| `PRIMA.CPP` (header) | `CELLSWARM` |
| `prima-conversations` (localStorage) | `cellswarm-conversations` |
| `prima-device-groups` (localStorage) | `cellswarm-device-groups` |
| `Prima.cpp Dashboard` (title) | `CellSwarm Dashboard` |

## Shell Scripts Renamed

| Old | New |
|-----|-----|
| `scripts/build_prima.sh` | `scripts/build_cellswarm.sh` |
| `scripts/build_rpc_server.sh` | `scripts/build_swarm_rpc.sh` |
| `scripts/bench_prima.sh` | `scripts/bench_cellswarm.sh` |
| `scripts/bench_prima_spec.sh` | `scripts/bench_cellswarm_spec.sh` |
| `scripts/bench_prima_phoneonly.sh` | `scripts/bench_cellswarm_phoneonly.sh` |
| `scripts/bench_prima_ethernet.sh` | `scripts/bench_cellswarm_ethernet.sh` |
| `scripts/bench_prima_dual_ring.sh` | `scripts/bench_cellswarm_dual_ring.sh` |

## Vendor / Submodule

| Old | New |
|-----|-----|
| `vendor/prima.cpp/` | `vendor/cellswarm/` |
| `.gitmodules` submodule name `vendor/prima.cpp` | `vendor/cellswarm` |

> The upstream URL (`gitee.com/zonghang-li/prima.cpp.git`) is unchanged — only the local directory name changed.

## Not Renamed (Upstream Internals)

These are internal llama.cpp/ggml C API names inside `vendor/cellswarm/` and are not part of CellSwarm branding:

- `llama_decode()`, `llama_model_load()`, etc. — C API functions
- `ggml_*` — GGML tensor library API
- `GGML_RPC_COMPRESS`, `GGML_RPC_DEBUG` — upstream env vars
- Contents of `vendor/cellswarm/` source files (upstream fork, not our branding)
