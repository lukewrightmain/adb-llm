# CellSwarm: Distributed LLM Inference on Android Phones

Run large language models (33B parameters) across a ring of Android phones using [cellswarm](https://github.com/nicojbae/cellswarm) pipeline-ring parallelism with speculative decoding.

**Peak result: 5.8 tok/s** on DeepSeek Coder 33B using 12 Samsung Galaxy Z Fold3 phones over Ethernet with interleaved speculative decoding + pipeline parallelism.

> This is the **production** branch — the best-performing, tested configuration. See [Benchmarks](#benchmarks) for full results and [Device Onboarding](#device-onboarding) to add phones to the cluster.

## Architecture

```
Phone 0 (rank 0)          Phone 1 (rank 1)         Phone N-1 (rank N-1)
+-----------------+       +-----------------+       +-----------------+
| Draft Model 1.3B|       |                 |       |                 |
| Target layers   |--ZMQ->| Target layers   |--...->| Target layers   |--+
| 0..K            |       | K+1..M          |       | ...62           |  |
+-----------------+       +-----------------+       +-----------------+  |
       ^                                                                 |
       +-----------------------------------------------------------------+
                              ZMQ ring (TCP over Ethernet)
```

Each phone holds a slice of the 33B model's layers. Tokens flow around the ring via ZeroMQ TCP sockets. Phone 0 also runs a small 1.3B draft model for speculative decoding — it drafts 8 candidate tokens, then all phones verify them simultaneously using pipeline parallelism.

## Requirements

### Hardware

- **Android phones** with ARM64 SoC (tested: Samsung Galaxy Z Fold3, Snapdragon 888)
  - Minimum ~5GB available RAM per phone
  - Need enough phones to fit the model (33B Q4_K_M = ~18.6 GiB, split across phones)
- **Network:** Ethernet adapters via USB-C hubs connected to a switch, OR USB ADB with tunnel scripts
- **Build server:** Linux x86_64 with Android NDK (for cross-compilation)

### Software

- Android 9+ (API level 28+)
- ADB access to all phones (either USB or TCP/IP)
- CMake >= 3.14
- Android NDK r26d (auto-downloaded by build script if missing)
- Python 3 (for helper scripts)

## Quick Start

### 1. Clone with Submodules

```bash
git clone --recursive <repo-url>
cd cellswarm
```

If you already cloned without `--recursive`:
```bash
git submodule update --init --recursive
```

The `vendor/` directory contains:
- `cellswarm` — Modified llama.cpp with ZMQ ring topology and pipeline parallelism
- `libzmq` — ZeroMQ messaging library
- `cppzmq` — C++ ZMQ bindings
- `HiGHS` — Optimization solver (host build only)

### 2. Build Binaries

```bash
./scripts/build_cellswarm.sh
```

This produces:
| Binary | Arch | Description |
|--------|------|-------------|
| `bin/cellswarm-worker` | ARM64 Android | Ring worker (non-rank-0 phones) |
| `bin/cellswarm-worker-spec` | ARM64 Android | Ring worker with speculative decoding (rank 0 phone) |
| `bin/cellswarm-host` | x86_64 Linux | Host ring node (optional, for host-in-ring mode) |
| `bin/cellswarm-host-spec` | x86_64 Linux | Host with speculative decoding |

Build flags for Snapdragon 888: `-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a78 -Ofast -fno-finite-math-only` (no `+i8mm` — Snapdragon 888 is ARMv8.4, i8mm needs ARMv8.6+). Binaries are stripped (~3MB vs 40MB unstripped).

### 3. Connect Phones via Ethernet

Each phone needs a USB-C Ethernet adapter connected to a shared Ethernet switch.

```bash
# Connect phones via ADB over TCP/IP
adb connect <phone-ip>:5555

# Verify connectivity
adb -s <phone-ip>:5555 shell ping -c 1 <other-phone-ip>
```

Expected phone-to-phone latency: 1-3ms.

**Important:** If phones are connected via ADB over TCP/IP (Ethernet), do NOT run `adb reboot` — this drops the Ethernet ADB connection. Reconnect with `adb connect <ip>:5555` if needed.

### 4. Deploy Model and Binaries to Phones

Download models:
- **Target:** [DeepSeek Coder 33B Instruct Q4_K_M](https://huggingface.co/TheBloke/deepseek-coder-33B-instruct-GGUF) (~18.6 GiB)
- **Draft:** [DeepSeek Coder 1.3B Instruct Q4_K_M](https://huggingface.co/TheBloke/deepseek-coder-1.3b-instruct-GGUF) (~832 MiB)

Push to phones:
```bash
# Push binary to all phones
ADB=~/.local/bin/adb
for ip in 10.105.0.12 10.105.0.13 ...; do
    $ADB -s ${ip}:5555 push bin/cellswarm-worker /data/local/tmp/cellswarm/bin/cellswarm-worker
    $ADB -s ${ip}:5555 shell chmod 755 /data/local/tmp/cellswarm/bin/cellswarm-worker
    $ADB -s ${ip}:5555 push deepseek-coder-33b-instruct.Q4_K_M.gguf /data/local/tmp/cellswarm/models/
done

# Push speculative binary + draft model to rank 0 only
$ADB -s 10.105.0.12:5555 push bin/cellswarm-worker-spec /data/local/tmp/cellswarm/bin/cellswarm-worker-spec
$ADB -s 10.105.0.12:5555 shell chmod 755 /data/local/tmp/cellswarm/bin/cellswarm-worker-spec
$ADB -s 10.105.0.12:5555 push deepseek-coder-1.3b-instruct.Q4_K_M.gguf /data/local/tmp/cellswarm/models/
```

### 5. Run Benchmark

Edit `scripts/bench_cellswarm_ethernet.sh` to set your phone IPs in the `ALL_PHONES` array, then:

```bash
# 10-phone speculative benchmark (best configuration)
./scripts/bench_cellswarm_ethernet.sh 10 --spec

# Non-speculative baseline
./scripts/bench_cellswarm_ethernet.sh 10

# Sweep all configurations
./scripts/bench_cellswarm_ethernet.sh 10 --sweep

# Different draft-max
./scripts/bench_cellswarm_ethernet.sh 10 --spec --draft-max 12
```

## Benchmark Script Reference

### `scripts/bench_cellswarm_ethernet.sh`

The main benchmark script for Ethernet-connected phones. No tunnels needed — phones communicate directly via IP.

```bash
Usage:
  ./scripts/bench_cellswarm_ethernet.sh <N_PHONES>                     # Non-speculative
  ./scripts/bench_cellswarm_ethernet.sh <N_PHONES> --spec              # Speculative decoding
  ./scripts/bench_cellswarm_ethernet.sh <N_PHONES> --spec --draft-max 24 --seed 100  # Production config
  ./scripts/bench_cellswarm_ethernet.sh <N_PHONES> --sweep             # Test 4,5,8,10,15,20 phones
  ./scripts/bench_cellswarm_ethernet.sh <N_PHONES> -fa -c 256          # Flash attention + small context
  ./scripts/bench_cellswarm_ethernet.sh <N_PHONES> -t 8 --taskset ff   # All cores (not recommended)
  MODEL=Q4_0 ./scripts/bench_cellswarm_ethernet.sh <N_PHONES>          # Use Q4_0 model
```

**What it does:**
1. Verifies all phones are reachable via ADB
2. Tests phone-to-phone connectivity (ping)
3. Calculates layer distribution across phones
4. Starts non-rank-0 workers, waits for model load (120s with `--no-mmap`)
5. Starts rank 0 with prompt, monitors for completion
6. Collects and displays timing metrics from all ranks

### `scripts/bench_cellswarm_phoneonly.sh`

Alternative benchmark for USB-connected phones using ADB tunnel chains (SSH relay through Windows PC). More complex setup but works without Ethernet.

### `scripts/build_cellswarm.sh`

Cross-compiles cellswarm for ARM64 Android and x86_64 Linux. Builds libzmq, HiGHS, and both worker/host binaries.

### `scripts/deploy_phones.sh`

Deploys binaries and models to USB-connected phones via ADB through a Windows PC relay.

### `scripts/deploy_model.sh`

Deploys Q4_0 models and speculative binaries. Supports `--target`, `--draft`, `--both`, `--all`, `--status`.

### `scripts/setup_ethernet.sh`

Configures static IPs and firewall rules on phones for Ethernet networking.

## Key Configuration

### On-Phone Paths

```
/data/local/tmp/cellswarm/
  bin/
    cellswarm-worker          # Ring worker binary
    cellswarm-worker-spec     # Speculative ring worker (rank 0 only)
  models/
    deepseek-coder-33b-instruct.Q4_K_M.gguf    # Target model (all phones)
    deepseek-coder-1.3b-instruct.Q4_K_M.gguf   # Draft model (rank 0 only)
```

### Critical Flags

| Flag | Purpose |
|------|---------|
| `--no-mmap` | **Required.** Without this, mmap page thrashing causes 87x slowdown |
| `--prefetch` | Prefetch model weights for better cache behavior |
| `taskset f0` | Pin to big cores (4-7) on Snapdragon 888 |
| `-t 4` | Use 4 threads (matches big core count) |
| `-c 512` | Context size (512 tokens) |
| `-lw <layers>` | Layer weight distribution, e.g. `5,7,7,7,6,6,6,6,6,6` for 10 phones |
| `--draft-max 8` | Maximum tokens to draft per speculative cycle |

### Layer Distribution

The layer distribution (`-lw`) controls how many of the 62 transformer layers each phone processes. Rank 0 gets fewer layers to leave room for the draft model.

For 10 phones: `-lw 5,7,7,7,6,6,6,6,6,6` (rank 0 = 5 layers, others = 6-7)

The benchmark script calculates this automatically.

### ZMQ Ports

| Port | Purpose |
|------|---------|
| 9000 | Data channel (ring PUSH/PULL) |
| 10000 | Signal channel (startup coordination) |

Each phone binds its recv socket on port `9000 + rank` and connects its send socket to the next phone's recv port.

## Device Onboarding

Step-by-step guide to add a new phone to the cluster.

### Prerequisites per Phone

- Samsung Galaxy Z Fold3 (SM-F926U/U1/W) or similar Snapdragon 888 device
- USB-C Ethernet adapter + Ethernet cable to shared switch
- ~5GB free RAM (close all apps, disable background processes)
- ~20GB free storage (for the 33B model)
- ADB debugging enabled (Settings > Developer Options > USB Debugging)

### Step 1: Enable ADB over TCP/IP

Connect the phone via USB first, then switch to TCP/IP:

```bash
ADB=~/.local/bin/adb

# If phone is new, connect via USB first to authorize
$ADB devices   # should show the phone

# Switch to TCP/IP mode
$ADB tcpip 5555

# Now connect over the network
$ADB connect <phone-ip>:5555

# Verify
$ADB -s <phone-ip>:5555 shell echo ok
```

### Step 2: Create Directory Structure

```bash
$ADB -s <phone-ip>:5555 shell "mkdir -p /data/local/tmp/cellswarm/bin /data/local/tmp/cellswarm/models"
```

### Step 3: Deploy Binaries

```bash
$ADB -s <phone-ip>:5555 push bin/cellswarm-worker /data/local/tmp/cellswarm/bin/cellswarm-worker
$ADB -s <phone-ip>:5555 push bin/cellswarm-worker-spec /data/local/tmp/cellswarm/bin/cellswarm-worker-spec
$ADB -s <phone-ip>:5555 shell "chmod 755 /data/local/tmp/cellswarm/bin/cellswarm-worker /data/local/tmp/cellswarm/bin/cellswarm-worker-spec"
```

### Step 4: Deploy Model

The 33B target model (~18GB) must be on every phone. The 1.3B draft model only needs to be on the rank 0 phone.

```bash
# Target model (ALL phones) — takes ~2 minutes per phone over Ethernet
$ADB -s <phone-ip>:5555 push models/deepseek-coder-33b-instruct.Q4_K_M.gguf \
  /data/local/tmp/cellswarm/models/deepseek-coder-33b-instruct.Q4_K_M.gguf

# Draft model (rank 0 phone ONLY)
$ADB -s <phone-ip>:5555 push models/deepseek-coder-1.3b-instruct.Q4_K_M.gguf \
  /data/local/tmp/cellswarm/models/deepseek-coder-1.3b-instruct.Q4_K_M.gguf
```

### Step 5: Verify Phone-to-Phone Connectivity

```bash
# From the new phone, ping an existing cluster phone
$ADB -s <new-phone-ip>:5555 shell "ping -c 3 <existing-phone-ip>"
# Expected: 1-3ms round trip
```

### Step 6: Add to Benchmark Script

Edit `scripts/bench_cellswarm_ethernet.sh` and add the new phone IP to the `ALL_PHONES` array:

```bash
ALL_PHONES=(
    10.105.0.12
    10.105.0.13
    ...
    <new-phone-ip>   # ADD HERE
)
```

### Step 7: Test

```bash
# Quick non-speculative test with just the new phone + 1 existing phone
./scripts/bench_cellswarm_ethernet.sh 2

# Full cluster test
./scripts/bench_cellswarm_ethernet.sh 12 --spec --draft-max 24 --seed 100 -n 128
```

### Batch Onboarding (Multiple Phones)

```bash
PHONES=("10.105.0.50" "10.105.0.51" "10.105.0.52")
ADB=~/.local/bin/adb

for ip in "${PHONES[@]}"; do
    echo "Onboarding $ip..."
    $ADB connect ${ip}:5555
    $ADB -s ${ip}:5555 shell "mkdir -p /data/local/tmp/cellswarm/bin /data/local/tmp/cellswarm/models"
    $ADB -s ${ip}:5555 push bin/cellswarm-worker /data/local/tmp/cellswarm/bin/cellswarm-worker
    $ADB -s ${ip}:5555 push bin/cellswarm-worker-spec /data/local/tmp/cellswarm/bin/cellswarm-worker-spec
    $ADB -s ${ip}:5555 shell "chmod 755 /data/local/tmp/cellswarm/bin/cellswarm-worker /data/local/tmp/cellswarm/bin/cellswarm-worker-spec"
    $ADB -s ${ip}:5555 push models/deepseek-coder-33b-instruct.Q4_K_M.gguf \
      /data/local/tmp/cellswarm/models/deepseek-coder-33b-instruct.Q4_K_M.gguf &
done
wait
echo "All phones onboarded. Model push may still be running in background."
```

### Warnings

- **DO NOT `adb reboot`** phones connected via Ethernet ADB — this drops the connection. Reconnect with `adb connect <ip>:5555`.
- **DO NOT build with `+i8mm`** — Snapdragon 888 is ARMv8.4 and will SIGILL.
- **Always use `--no-mmap`** — without it, mmap page thrashing causes 87x slowdown.
- Each phone needs **120 seconds** after starting to load the model with `--no-mmap`. The benchmark script handles this automatically.

---

## Benchmarks

### Production Configuration (Best Throughput)

12-phone Ethernet ring, DeepSeek Coder 33B Q4_K_M, speculative decoding with 1.3B draft model:

```
Phones:     12x Samsung Galaxy Z Fold3 (Snapdragon 888)
Model:      DeepSeek Coder 33B Q4_K_M (62 layers, ~18.6 GiB)
Draft:      DeepSeek Coder 1.3B Q4_K_M (~832 MiB)
Settings:   --no-mmap -t 4 taskset f0 --draft-max 24 --seed 100
Transport:  Ethernet direct IP (phone-to-phone, no tunnels)
```

| Metric | Value |
|--------|-------|
| **Decode speed** | **5.8 tok/s** |
| Acceptance rate | 74.5% |
| Pipeline recv time | ~2260ms per cycle |
| Draft time | ~920ms per cycle |
| Per-layer compute | ~12ms |
| Per-hop total | ~65ms (compute + overhead) |

### Scaling Results (Interleaved Pipeline, d24, seed 100)

| Phones | tok/s | Accept% | Notes |
|--------|-------|---------|-------|
| 8 | 4.7 | 74.5% | |
| 10 | 5.5 | 74.5% | |
| 11 | 6.1 | 74.5% | |
| **12** | **5.8** | **74.5%** | **Production config** |
| 15 | 5.8 | 74.5% | Diminishing returns |
| 20 | 5.6 | 74.5% | Communication overhead grows |

Sweet spot: **11-12 phones** for single-request throughput. Beyond 12, more hops add more overhead than fewer layers per phone saves.

### Optimization History

| Milestone | tok/s | Change |
|-----------|-------|--------|
| Non-speculative baseline (any phone count) | 1.04 | — |
| Speculative d8, 10 phones | 3.3 | +3.2x |
| Interleaved pipeline, d24 | 6.1 | +1.8x |
| Packed metadata + stripped binary | 5.8 | Consistent same-session |

> Note: Absolute numbers vary between sessions (thermal, background load). The 6.1 and 5.8 results are from different sessions. Same-session A/B testing confirmed packed metadata gives **+26% improvement** over baseline code (4.6 → 5.8 tok/s).

### What Doesn't Help

Tested and rejected (see [docs/compute-optimization-results.md](docs/compute-optimization-results.md)):

| Optimization | Result | Why |
|---|---|---|
| Q4_0 quantization | -44% | Repacked GEMV removed; lower quality tanks acceptance |
| Flash attention (`-fa`) | ~0% | Attention is <5% of decode compute |
| Context reduction (`-c 256`) | ~0% | KV cache already small |
| More threads (`-t 8`) | -22% | A55 little cores slow down GEMV |
| Fewer threads (`-t 3`) | -45% | Not enough parallelism |
| `-Ofast` compiler flag | ~0% | NEON intrinsics dominate the hot path |

---

## How It Works

### Ring Topology

Phones form a ring. Each phone:
1. **Receives** an activation tensor from the previous phone
2. **Computes** its assigned layers (e.g., layers 5-11)
3. **Sends** the result to the next phone

### Speculative Decoding

Rank 0 runs a small 1.3B draft model to predict what the 33B model will generate:
1. **Draft:** Generate 8 candidate tokens with the 1.3B model (~300ms on-phone)
2. **Verify:** Send all 9 tokens (8 drafted + 1 resync) through the ring for 33B verification
3. **Accept:** Keep tokens where draft and target agree (~78% at 10 phones)
4. **Repeat:** Resync draft model, generate next batch

### Pipeline Parallelism

Without pipeline parallelism, each verification token must complete the full ring before the next starts. With it:
1. **Phase 1 (Send):** Rank 0 sends all 9 token embeddings into the ring back-to-back
2. **Overlap:** While phone 1 processes token 2, phone 2 processes token 1, phone 3 processes token 0...
3. **Phase 2 (Recv):** Rank 0 receives completed tokens as they exit the ring

This transforms the bottleneck from `9 * ring_latency` to `ring_latency + 8 * per_phone_compute`.

## Results

See [RESULTS.md](RESULTS.md) for full benchmark history and [docs/compute-optimization-results.md](docs/compute-optimization-results.md) for optimization experiments.

**TL;DR:** 12 phones at ~5.8 tok/s with interleaved speculative decoding + pipeline parallelism.

## Project Structure

```
cellswarm/
  bin/                      # Compiled binaries (not committed, build from source)
  scripts/
    bench_cellswarm_ethernet.sh # Ethernet phone benchmark
    bench_cellswarm_phoneonly.sh # USB phone benchmark (tunnel-based)
    bench_cellswarm.sh          # Host+phone benchmark
    bench_cellswarm_spec.sh     # Speculative decoding benchmark
    build_cellswarm.sh          # Cross-compilation script
    deploy_phones.sh        # Deploy binaries/models to phones
    deploy_model.sh         # Deploy Q4_0 models
    setup_ethernet.sh       # Configure phone Ethernet networking
  src/cellswarm/              # Python orchestration code
  vendor/
    cellswarm/              # Modified llama.cpp with ring topology
    libzmq/                 # ZeroMQ messaging
    cppzmq/                 # C++ ZMQ bindings
    HiGHS/                  # Optimization solver
  RESULTS.md                # Detailed benchmark results
  README.md                 # This file
```

## Troubleshooting

### Phone runs out of memory
Reduce the number of layers per phone by adding more phones to the ring, or use a smaller context size (`-c 256`).

### Workers fail to start
Check the phone log: `adb -s <ip>:5555 shell cat /data/local/tmp/cellswarm-worker.log`

Common issues:
- Model file missing or corrupted (re-push)
- Port already in use (kill old workers: `adb shell pkill -9 cellswarm-worker`)
- Not enough RAM (close other apps, reduce layers)

### Very slow performance (~0.01 tok/s)
You're probably missing `--no-mmap`. This is the #1 issue. Without it, Android's mmap implementation thrashes pages, causing 87x slowdown.

### SIGILL crash
Your ARM flags include `+i8mm` but the phone doesn't support it. Snapdragon 888 is ARMv8.4 (no i8mm). Rebuild with `-march=armv8.2-a+dotprod+fp16` only.

### Bcast startup timeout
Phones can't reach each other. Verify with `adb shell ping <other-phone-ip>`. Check firewall: `adb shell iptables -L INPUT`.

## License

This project builds on [cellswarm](https://github.com/nicojbae/cellswarm) (MIT License) and [llama.cpp](https://github.com/ggerganov/llama.cpp) (MIT License).
