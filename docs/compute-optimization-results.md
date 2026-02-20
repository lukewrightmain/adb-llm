# Compute Optimization Results: 60ms Per-Layer Analysis

## Overview

Investigation into reducing per-layer compute time (~12ms/layer, ~60ms/hop) on Snapdragon 888 for Q4_K_M inference. Tested compiler flags, quantization, flash attention, context reduction, and thread configurations.

**Result: 12ms/layer is a hard floor on this hardware for Q4_K_M. Packed metadata remains the biggest win (+26%). Compiler/quantization/attention optimizations are ineffective.**

## Hardware

- **Phones**: 12x Samsung Galaxy Z Fold3 (SM-F926U), Snapdragon 888
  - CPU: 1x Cortex-X1 (2.84GHz) + 3x Cortex-A78 (2.4GHz) + 4x Cortex-A55 (1.8GHz)
  - No i8mm (ARMv8.4, needs ARMv8.6+ for SDOT fast paths)
  - No root (can't change governor, scheduler, or renice)
- **Connection**: Ethernet, direct phone-to-phone IP (2-4ms latency)
- **Model**: DeepSeek Coder 33B Instruct Q4_K_M (62 layers, ~20GB)
- **Draft**: DeepSeek Coder 1.3B Instruct Q4_K_M (~850MB, rank 0 only)
- **Baseline settings**: `--no-mmap -t 4 -tb 4 taskset f0 -c 512 --draft-max 24 --seed 100`

## Same-Session A/B Test (Definitive Comparison)

All tests run back-to-back on the same 12 phones in the same session, eliminating thermal/environmental variance.

| Config | tok/s | Pipeline recv | Accept% | Binary size |
|--------|-------|---------------|---------|-------------|
| Baseline code + `-O3 -g` (40MB binary) | 4.625 | 3080ms | 74.5% | 40MB |
| Packed metadata + `-Ofast` + stripped | **5.816** | **2261ms** | 74.5% | **3.1MB** |

**+26% throughput improvement**, entirely from the packed metadata code change reducing pipeline overhead from 3080ms to 2261ms per cycle.

## Optimization 1: Compiler Flags

Changed `build_cellswarm.sh` CFLAGS/CXXFLAGS from:
```
-march=armv8.2-a+dotprod+fp16
```
To:
```
-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a78 -Ofast -fno-finite-math-only -ffunction-sections -fdata-sections
```
Plus LDFLAGS: `-Wl,--gc-sections -Wl,--strip-all`

| Flag | Expected | Actual | Why |
|------|----------|--------|-----|
| `-Ofast` (replaces `-O3`) | +2-3% | ~0% | Hot path is NEON intrinsics, not compiler-optimizable C |
| `-mcpu=cortex-a78` | +1-2% | ~0% | Pipeline scheduling doesn't help with NEON dot products |
| `--gc-sections --strip-all` | Binary size | 40MB → 3.1MB | Dead code elimination + symbol stripping |
| `-fno-finite-math-only` | Required | Required | ggml.c compile-time check rejects `-ffinite-math-only` (implied by `-Ofast`) |

**Verdict**: Binary size win (13x smaller), negligible speed impact. The Q4_K_M dot product path in `ggml-quants.c` is dominated by NEON intrinsics that the compiler can't improve.

## Optimization 2: Q4_0 Quantization

Hypothesis: `ggml-aarch64.c` has hand-optimized NEON GEMV kernels for Q4_0 (`ggml_gemv_q4_0_4x4_q8_0`, `ggml_gemv_q4_0_4x8_q8_0`, `ggml_gemv_q4_0_8x8_q8_0`) that should be faster than the generic Q4_K_M path.

Reality: These kernels require **repacked** quantization types (`GGML_TYPE_Q4_0_4_4`, `GGML_TYPE_Q4_0_4_8`, `GGML_TYPE_Q4_0_8_8`) which have been **removed** from this version of cellswarm/llama.cpp:

```c
// ggml.h:388-390
// GGML_TYPE_Q4_0_4_4 = 31, support has been removed from gguf files
// GGML_TYPE_Q4_0_4_8 = 32,
// GGML_TYPE_Q4_0_8_8 = 33,
```

Standard Q4_0 uses the same generic `ggml_vec_dot_q4_0_q8_0` path — no specialized GEMV.

| Model | tok/s | Per-layer | Accept% | Notes |
|-------|-------|-----------|---------|-------|
| Q4_K_M | 5.874 | ~12ms | 74.5% | Baseline |
| Q4_0 | 3.298 | ~14ms | **40.7%** | Slower + much worse quality |

Q4_0 is worse on both axes:
1. **Compute**: 14ms/layer vs 12ms (Q4_K_M k-quants have better dequant routines)
2. **Acceptance**: 40.7% vs 74.5% (lower quantization quality diverges from draft model predictions, causing massive rejection rates)

**Verdict**: Dead end. Q4_0 repacked GEMV was the only path to faster compute, and it's been removed.

## Optimization 3: Flash Attention (`-fa`) + Context Reduction (`-c 256`)

Flash attention computes attention in-place, avoiding materializing the full attention matrix.

| Config | tok/s | Graph nodes | Notes |
|--------|-------|-------------|-------|
| `-c 512` (default) | 5.874 | 160 | Baseline |
| `-fa -c 256` | 5.885 | 141 | Flash attn active, smaller KV cache |

Flash attention does reduce the compute graph (160 → 141 nodes), but for single-token decode with <200 tokens context, attention is a tiny fraction of total compute. The GEMV/dot product operations dominate.

**Verdict**: Negligible improvement (~0.2%). Only meaningful for long-context prompt evaluation.

## Optimization 4: Thread Sweep

Snapdragon 888 core layout with `taskset` masks:

| Cores | Mask | Type |
|-------|------|------|
| 0-3 | `0f` | Cortex-A55 (little, 1.8GHz) |
| 4-6 | `70` | Cortex-A78 (big, 2.4GHz) |
| 7 | `80` | Cortex-X1 (prime, 2.84GHz) |
| 4-7 | `f0` | All big cores |
| 0-7 | `ff` | All cores |

| Threads | Taskset | tok/s | Change | Notes |
|---------|---------|-------|--------|-------|
| 3 | `f0` | 3.231 | -45% | Fewer threads = slower GEMV per layer |
| **4** | **`f0`** | **5.816** | **baseline** | **3x A78 + 1x X1 (optimal)** |
| 8 | `ff` | 4.533 | -22% | A55 cores drag down parallel GEMV reduction |

When using all 8 cores, the GEMV thread pool waits for the slowest thread. A55 cores run NEON at ~1/3 the speed of A78, making them a bottleneck in parallel reduction.

**Verdict**: 4 threads on big cores (taskset f0) is optimal. Do not use little cores for GEMV.

## Per-Layer Compute Breakdown

Steady-state timing from rank 11 (5 layers, l_out-56):

```
compute=63ms  (5 layers, ~12.6ms each)
compute=68ms  (5 layers, ~13.6ms each, with recv overlap)
compute=63ms
compute=68ms
...
```

Per-layer: **12-14ms** consistently across all optimization attempts. This is the hardware floor for Q4_K_M on Cortex-A78 at 2.4GHz without i8mm.

## Key Learnings

1. **Packed metadata is the real win**: Replacing 14-frame ZMQ multipart with single binary frame saves ~800ms/cycle (3080ms → 2260ms pipeline time), yielding +26% throughput.

2. **Compiler flags can't improve NEON intrinsics**: The Q4_K_M hot path in `ggml-quants.c` and `ggml-aarch64.c` is hand-written NEON assembly. `-Ofast`, `-mcpu`, and optimization flags have no measurable effect on it.

3. **Q4_0 repacked GEMV was removed**: The specialized ARM NEON kernels (`ggml_gemv_q4_0_4x4_q8_0` etc.) require repacked quantization types that have been deleted from the codebase. Standard Q4_0 is slower AND has worse quality.

4. **Snapdragon 888 big.LITTLE is a trap**: A55 little cores hurt parallel GEMV. Always pin to big cores only (`taskset f0`).

5. **Flash attention is irrelevant for decode**: For single-token generation at short context, attention is <5% of compute time. Only matters for long prompt evaluation.

6. **12ms/layer is the hardware floor**: To break below this, you need either:
   - i8mm support (ARMv8.6+, e.g., Cortex-A710/X2 in Snapdragon 8 Gen 1+)
   - Working GPU compute (Adreno 750+ for Vulkan, currently crashes on Adreno 660)
   - Different quantization format with hand-optimized NEON kernels

## Branches

| Branch | Description |
|--------|-------------|
| `optimization-packed-metadata` | All optimizations: packed metadata, -Ofast, stripped binary, bench improvements |
| `baseline-6.1-toks` | Known-good state before optimization experiments |

## Reproduction

```bash
# Optimized binary (current best)
./scripts/build_cellswarm.sh
# Deploy to phones, then:
./scripts/bench_cellswarm_ethernet.sh 12 --spec --draft-max 24 --seed 100 -n 128

# Baseline comparison (switch submodule to baseline-6.1-toks first)
cd vendor/cellswarm && git checkout baseline-6.1-toks && cd ../..
./scripts/build_cellswarm.sh
# Redeploy, then run same bench command
```

## Date

Benchmarks run: February 19, 2026
