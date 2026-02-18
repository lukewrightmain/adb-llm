# adb-llm: Distributed LLM Inference on Android Phones

Run large language models (33B parameters) across a ring of Android phones using [prima.cpp](https://github.com/nicojbae/prima.cpp) pipeline-ring parallelism with speculative decoding.

**Peak result: 3.345 tok/s** on DeepSeek Coder 33B using 10 Samsung Galaxy Z Fold3 phones over Ethernet.

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
cd adb-llm
```

If you already cloned without `--recursive`:
```bash
git submodule update --init --recursive
```

The `vendor/` directory contains:
- `prima.cpp` — Modified llama.cpp with ZMQ ring topology and pipeline parallelism
- `libzmq` — ZeroMQ messaging library
- `cppzmq` — C++ ZMQ bindings
- `HiGHS` — Optimization solver (host build only)

### 2. Build Binaries

```bash
./scripts/build_prima.sh
```

This produces:
| Binary | Arch | Description |
|--------|------|-------------|
| `bin/prima-worker` | ARM64 Android | Ring worker (non-rank-0 phones) |
| `bin/prima-worker-spec` | ARM64 Android | Ring worker with speculative decoding (rank 0 phone) |
| `bin/prima-host` | x86_64 Linux | Host ring node (optional, for host-in-ring mode) |
| `bin/prima-host-spec` | x86_64 Linux | Host with speculative decoding |

Build flags for Snapdragon 888: `-march=armv8.2-a+dotprod+fp16` (no `+i8mm` — Snapdragon 888 is ARMv8.4, i8mm needs ARMv8.6+).

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
    $ADB -s ${ip}:5555 push bin/prima-worker /data/local/tmp/adb-llm/bin/prima-worker
    $ADB -s ${ip}:5555 shell chmod 755 /data/local/tmp/adb-llm/bin/prima-worker
    $ADB -s ${ip}:5555 push deepseek-coder-33b-instruct.Q4_K_M.gguf /data/local/tmp/adb-llm/models/
done

# Push speculative binary + draft model to rank 0 only
$ADB -s 10.105.0.12:5555 push bin/prima-worker-spec /data/local/tmp/adb-llm/bin/prima-worker-spec
$ADB -s 10.105.0.12:5555 shell chmod 755 /data/local/tmp/adb-llm/bin/prima-worker-spec
$ADB -s 10.105.0.12:5555 push deepseek-coder-1.3b-instruct.Q4_K_M.gguf /data/local/tmp/adb-llm/models/
```

### 5. Run Benchmark

Edit `scripts/bench_prima_ethernet.sh` to set your phone IPs in the `ALL_PHONES` array, then:

```bash
# 10-phone speculative benchmark (best configuration)
./scripts/bench_prima_ethernet.sh 10 --spec

# Non-speculative baseline
./scripts/bench_prima_ethernet.sh 10

# Sweep all configurations
./scripts/bench_prima_ethernet.sh 10 --sweep

# Different draft-max
./scripts/bench_prima_ethernet.sh 10 --spec --draft-max 12
```

## Benchmark Script Reference

### `scripts/bench_prima_ethernet.sh`

The main benchmark script for Ethernet-connected phones. No tunnels needed — phones communicate directly via IP.

```bash
Usage:
  ./scripts/bench_prima_ethernet.sh <N_PHONES>                # Non-speculative
  ./scripts/bench_prima_ethernet.sh <N_PHONES> --spec         # Speculative decoding
  ./scripts/bench_prima_ethernet.sh <N_PHONES> --sweep        # Test 4,5,8,10,15,20 phones
  ./scripts/bench_prima_ethernet.sh <N_PHONES> --draft-max N  # Custom draft batch size
  MODEL=Q4_0 ./scripts/bench_prima_ethernet.sh <N_PHONES>     # Use Q4_0 model
```

**What it does:**
1. Verifies all phones are reachable via ADB
2. Tests phone-to-phone connectivity (ping)
3. Calculates layer distribution across phones
4. Starts non-rank-0 workers, waits for model load (120s with `--no-mmap`)
5. Starts rank 0 with prompt, monitors for completion
6. Collects and displays timing metrics from all ranks

### `scripts/bench_prima_phoneonly.sh`

Alternative benchmark for USB-connected phones using ADB tunnel chains (SSH relay through Windows PC). More complex setup but works without Ethernet.

### `scripts/build_prima.sh`

Cross-compiles prima.cpp for ARM64 Android and x86_64 Linux. Builds libzmq, HiGHS, and both worker/host binaries.

### `scripts/deploy_phones.sh`

Deploys binaries and models to USB-connected phones via ADB through a Windows PC relay.

### `scripts/deploy_model.sh`

Deploys Q4_0 models and speculative binaries. Supports `--target`, `--draft`, `--both`, `--all`, `--status`.

### `scripts/setup_ethernet.sh`

Configures static IPs and firewall rules on phones for Ethernet networking.

## Key Configuration

### On-Phone Paths

```
/data/local/tmp/adb-llm/
  bin/
    prima-worker          # Ring worker binary
    prima-worker-spec     # Speculative ring worker (rank 0 only)
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

See [RESULTS.md](RESULTS.md) for detailed benchmark results and analysis.

**TL;DR:** 10 phones at 3.345 tok/s is the sweet spot for DeepSeek Coder 33B Q4_K_M.

## Project Structure

```
adb-llm/
  bin/                      # Compiled binaries (not committed, build from source)
  scripts/
    bench_prima_ethernet.sh # Ethernet phone benchmark
    bench_prima_phoneonly.sh # USB phone benchmark (tunnel-based)
    bench_prima.sh          # Host+phone benchmark
    bench_prima_spec.sh     # Speculative decoding benchmark
    build_prima.sh          # Cross-compilation script
    deploy_phones.sh        # Deploy binaries/models to phones
    deploy_model.sh         # Deploy Q4_0 models
    setup_ethernet.sh       # Configure phone Ethernet networking
  src/adb_llm/              # Python orchestration code
  vendor/
    prima.cpp/              # Modified llama.cpp with ring topology
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
Check the phone log: `adb -s <ip>:5555 shell cat /data/local/tmp/prima-worker.log`

Common issues:
- Model file missing or corrupted (re-push)
- Port already in use (kill old workers: `adb shell pkill -9 prima-worker`)
- Not enough RAM (close other apps, reduce layers)

### Very slow performance (~0.01 tok/s)
You're probably missing `--no-mmap`. This is the #1 issue. Without it, Android's mmap implementation thrashes pages, causing 87x slowdown.

### SIGILL crash
Your ARM flags include `+i8mm` but the phone doesn't support it. Snapdragon 888 is ARMv8.4 (no i8mm). Rebuild with `-march=armv8.2-a+dotprod+fp16` only.

### Bcast startup timeout
Phones can't reach each other. Verify with `adb shell ping <other-phone-ip>`. Check firewall: `adb shell iptables -L INPUT`.

## License

This project builds on [prima.cpp](https://github.com/nicojbae/prima.cpp) (MIT License) and [llama.cpp](https://github.com/ggerganov/llama.cpp) (MIT License).
