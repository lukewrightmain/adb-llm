# Snapdragon Chipset Reference for cellswarm

Build flags, performance estimates, and onboarding guide for Snapdragon SoCs (2021-2026) running our cellswarm distributed inference pipeline.

---

## Quick Reference Table

| SoC | Year | ARM ISA | Big Cores | i8mm | SVE2 | dotprod | Est. ms/layer | GPU Compute |
|-----|------|---------|-----------|:----:|:----:|:-------:|:-------------:|:-----------:|
| SD 870 | 2021 | v8.2 | 1x A77 @3.2 + 3x A77 @2.4 | No | No | Yes | ~14 | Adreno 650 (no) |
| **SD 888** | **2021** | **v8.2** | **1x X1 @2.84 + 3x A78 @2.4** | **No** | **No** | **Yes** | **12 (measured)** | **Adreno 660 (crashes)** |
| SD 8 Gen 1 | 2022 | v9.0 | 1x X2 @3.0 + 3x A710 @2.5 | Yes | Yes | Yes | ~8-9 | Adreno 730 (maybe) |
| SD 8+ Gen 1 | 2022 | v9.0 | 1x X2 @3.2 + 3x A710 @2.75 | Yes | Yes | Yes | ~7-8 | Adreno 730 (maybe) |
| SD 8 Gen 2 | 2023 | v9.2 | 1x X3 @3.36 + 2x A715 @2.8 + 2x A710 @2.8 | Yes | Yes | Yes | ~6-7 | Adreno 740 (likely) |
| SD 8 Gen 3 | 2024 | v9.2 | 1x X4 @3.3 + 3x A720 @3.15 + 2x A720 @2.96 | Yes | Yes | Yes | ~5-6 | Adreno 750 (works) |
| SD 8 Elite | 2025 | v8.7 | 2x Oryon @4.32 + 6x Oryon @3.53 | Yes | No | Yes | ~3-4 | Adreno 830 (likely) |

**Bold row** = our measured reference (Samsung Galaxy Z Fold3, Snapdragon 888).

---

## Build Flags Per SoC

### Snapdragon 870

```bash
CFLAGS="-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a77 -Ofast -fno-finite-math-only"
THREADS=4          # 1x A77 @3.2 + 3x A77 @2.4
TASKSET="f0"       # Cores 4-7 (big cluster)
```

Common phones: Poco F3, OnePlus 9R, Motorola Edge S

### Snapdragon 888 (Reference)

```bash
CFLAGS="-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a78 -Ofast -fno-finite-math-only"
THREADS=4          # 1x X1 @2.84 + 3x A78 @2.4 (use A78 cluster)
TASKSET="f0"       # Cores 4-7 (big A78 cores)
```

**WARNING:** Do NOT add `+i8mm`. Snapdragon 888 is ARMv8.4 (ISA reports v8.2 for compiler), i8mm requires ARMv8.6+. Building with `+i8mm` causes SIGILL at runtime.

Common phones: Galaxy Z Fold3, Galaxy S21, Xiaomi Mi 11, OnePlus 9 Pro

### Snapdragon 8 Gen 1

```bash
CFLAGS="-march=armv9-a+i8mm+sve2+dotprod+fp16 -mcpu=cortex-a710 -Ofast -fno-finite-math-only"
THREADS=4          # 3x A710 @2.5 (use big cluster, skip X2 prime)
TASKSET="f0"       # Cores 4-7 (big A710 cluster, may vary by OEM)
```

**i8mm enabled**: SMMLA instruction available. Q4_0 repacked GEMV kernels will work. Expected ~1.5x speedup over SD 888 from i8mm alone.

Common phones: Galaxy S22, OnePlus 10 Pro, Xiaomi 12 Pro

### Snapdragon 8+ Gen 1

```bash
CFLAGS="-march=armv9-a+i8mm+sve2+dotprod+fp16 -mcpu=cortex-a710 -Ofast -fno-finite-math-only"
THREADS=4          # 3x A710 @2.75
TASKSET="f0"       # Cores 4-7
```

Same ISA as 8 Gen 1 but ~10% higher clocks on the big cores. Better thermal design (TSMC 4nm vs Samsung 4nm).

Common phones: Galaxy Z Fold4, Galaxy S22+, OnePlus 10T

### Snapdragon 8 Gen 2

```bash
CFLAGS="-march=armv9.2-a+i8mm+sve2+dotprod+fp16 -mcpu=cortex-x3 -Ofast -fno-finite-math-only"
THREADS=4          # 2x A715 @2.8 + 2x A710 @2.8 (4 big cores)
TASKSET="f0"       # Cores 4-7 (big cluster)
```

Heterogeneous big core cluster (A715 + A710). Both support i8mm. The Cortex-X3 prime core is fastest but taskset should cover the big cluster for consistent GEMV.

Common phones: Galaxy S23, OnePlus 11, Xiaomi 13 Pro

### Snapdragon 8 Gen 3

```bash
CFLAGS="-march=armv9.2-a+i8mm+sve2+dotprod+fp16 -mcpu=cortex-x4 -Ofast -fno-finite-math-only"
THREADS=5          # 3x A720 @3.15 + 2x A720 @2.96 (5 big cores)
TASKSET="1f0"      # Cores 4-8 (5 big cores)
```

First SoC where Adreno GPU (750) reliably runs llama.cpp Vulkan/OpenCL compute shaders. 5 big cores available — increase thread count to 5.

Common phones: Galaxy S24, OnePlus 12, Xiaomi 14 Pro

### Snapdragon 8 Elite (2025)

```bash
CFLAGS="-march=armv8.7-a+i8mm+dotprod+fp16 -Ofast -fno-finite-math-only"
THREADS=6          # 6x Oryon @3.53 (all cores are "big")
TASKSET="3f0"      # Cores 4-9 (or all 8 cores, Oryon has no little cores)
```

**WARNING:** No SVE2 — Qualcomm Oryon cores are based on Nuvia Phoenix (ARMv8.7, not ARMv9). Do NOT add `+sve2`.

Oryon cores are significantly wider than Cortex — expect ~2.5-3x single-thread performance over A78. All 8 cores are performance cores (no little/big split), so higher thread counts are viable.

Common phones: Galaxy S25, OnePlus 13, Xiaomi 15 Pro

---

## Performance Estimation Methodology

All estimates are extrapolated from our measured baseline:

**Baseline:** Snapdragon 888, Cortex-A78 @ 2.4 GHz, 4 threads, Q4_K_M = **12 ms/layer**

### Scaling formula

```
estimated_ms = baseline_ms * (baseline_clock / target_clock) * i8mm_factor

where:
  baseline_ms    = 12 ms
  baseline_clock = 2.4 GHz (Cortex-A78)
  target_clock   = big core clock of target SoC
  i8mm_factor    = 1.0 if no i8mm, 0.65 if i8mm available
```

The 0.65 factor for i8mm comes from ARM's published benchmarks showing SMMLA gives ~1.5x speedup for quantized int8 matrix multiply, which dominates the Q4_K_M decode path.

### Estimated per-layer times

| SoC | Big Core Clock | i8mm | Calculation | Est. ms/layer |
|-----|---------------|------|-------------|---------------|
| SD 870 | 2.4 GHz (A77) | No | 12 * (2.4/2.4) * 1.0 | 12 ms |
| SD 888 | 2.4 GHz (A78) | No | measured | **12 ms** |
| SD 8 Gen 1 | 2.5 GHz (A710) | Yes | 12 * (2.4/2.5) * 0.65 | 7.5 ms |
| SD 8+ Gen 1 | 2.75 GHz (A710) | Yes | 12 * (2.4/2.75) * 0.65 | 6.8 ms |
| SD 8 Gen 2 | 2.8 GHz (A715) | Yes | 12 * (2.4/2.8) * 0.65 | 6.7 ms |
| SD 8 Gen 3 | 3.15 GHz (A720) | Yes | 12 * (2.4/3.15) * 0.65 | 5.9 ms |
| SD 8 Elite | 3.53 GHz (Oryon) | Yes | 12 * (2.4/3.53) * 0.65 | 5.3 ms* |

*Oryon has wider execution units than Cortex, so real performance may be better than clock-scaling suggests. The 3-4 ms estimate in the quick reference table accounts for Oryon's wider issue width.

### Important caveats

- These are **estimates**. Always benchmark on real hardware.
- i8mm speedup depends on the quantization type. Q4_0 benefits most (repacked GEMV kernels use SMMLA directly). Q4_K_M benefits less directly.
- Clock scaling is approximate — IPC improvements between core generations are not captured.
- Thermal throttling on phones can reduce sustained performance by 10-30%.
- SD 870's A77 cores are slightly lower IPC than SD 888's A78 cores at the same clock.

---

## i8mm: What It Means for Inference

### Background

`i8mm` (Int8 Matrix Multiply) adds the `SMMLA` instruction to ARM, which computes 8-bit signed integer matrix multiply-accumulate in a single instruction. This is critical for quantized LLM inference because:

1. Q4_0 and Q4_K quantized weights are unpacked to int8 before GEMV
2. Without SMMLA, the multiply-accumulate uses `SDOT` (dotprod) which handles 4 elements per instruction
3. With SMMLA, 8 elements are processed per instruction in a 2x4 accumulation pattern

### Impact on our pipeline

| Feature | Without i8mm (SD 888) | With i8mm (SD 8 Gen 1+) |
|---------|----------------------|------------------------|
| Q4_K_M GEMV | NEON dotprod path | SMMLA path (~1.5x faster) |
| Q4_0 GEMV | NEON dotprod path | Repacked SMMLA path (~1.8x faster) |
| Build flag | `-march=armv8.2-a+dotprod+fp16` | `-march=armv9-a+i8mm+sve2+dotprod+fp16` |
| Binary compat | Runs on ARMv8.2+ | Crashes (SIGILL) on pre-i8mm SoCs |

### Mixed fleet warning

If your fleet has both i8mm and non-i8mm phones, you need **separate binaries**:
- `cellswarm-worker-v82` for SD 870/888 (no i8mm)
- `cellswarm-worker-v9` for SD 8 Gen 1+ (with i8mm)

See "Multi-Device Fleet" section below.

---

## GPU Compute Status

### Adreno GPU compatibility with llama.cpp

| GPU | SoC | OpenCL | Vulkan | Recommendation |
|-----|-----|:------:|:------:|----------------|
| Adreno 650 | SD 870 | Crashes | Crashes | CPU only |
| Adreno 660 | SD 888 | Crashes (driver bug) | DeviceLost | CPU only |
| Adreno 730 | SD 8 Gen 1/+ | Untested | Untested | Test first; likely unstable |
| Adreno 740 | SD 8 Gen 2 | Likely works | Likely works | Test; may be worth ngl=10-20 |
| Adreno 750 | SD 8 Gen 3 | **Works** | **Works** | Use ngl for large layers |
| Adreno 830 | SD 8 Elite | Likely works | Likely works | Expected to work |

### Adreno 660 failure details (tested Feb 2026)

- **OpenCL**: Kernel compiler crashes during `clBuildProgram`. The driver (dated 09/2023) is too old for llama.cpp's OpenCL kernels. No way to update on most phones.
- **Vulkan**: Initializes successfully (Vulkan 1.1, all extensions present). Crashes with `vk::DeviceLostError` on compute shader dispatch. Even `ngl=1` (single layer offload) crashes. BLAS-via-Vulkan (before crash) was 2x slower than NEON CPU.

### GPU offload guidelines

For SoCs with working GPU compute (SD 8 Gen 3+):
- Use `--ngl N` to offload N layers to GPU
- Start with `ngl=10` and benchmark, then increase
- GPU helps most with prompt eval (batched) and less with single-token decode
- In our ring topology, GPU offload is per-rank (each phone offloads its own layers)
- Monitor GPU memory — LPDDR is shared between CPU and GPU

---

## RAM Availability by Phone Model

Available RAM after Android OS overhead (measured with `free -m` via ADB):

| Phone | SoC | Total RAM | Available RAM | Notes |
|-------|-----|-----------|---------------|-------|
| Galaxy Z Fold3 (SM-F926U) | SD 888 | 12 GB | **5.0-5.6 GB** | Android 14, our fleet |
| Galaxy Z Fold3 (SM-F926U) | SD 888 | 12 GB | **5.0-5.6 GB** | Android 15, ethernet fleet |
| Galaxy S21 | SD 888 | 8 GB | ~3.5 GB | Less headroom |
| Galaxy S22 | SD 8 Gen 1 | 8 GB | ~3.5 GB | |
| Galaxy S23 | SD 8 Gen 2 | 8 GB | ~3.8 GB | Better memory management |
| Galaxy S24 | SD 8 Gen 3 | 8-12 GB | ~4.0-6.0 GB | |
| Galaxy Z Fold4 | SD 8+ Gen 1 | 12 GB | ~5.0 GB | |
| Galaxy Z Fold5 | SD 8 Gen 2 | 12 GB | ~5.5 GB | |
| Galaxy Z Fold6 | SD 8 Gen 3 | 12 GB | ~5.5 GB | |
| Galaxy S25 Ultra | SD 8 Elite | 12-16 GB | ~6.0-8.0 GB | |

**Key rule:** `--no-mmap` is mandatory on all phones. Without it, mmap page thrashing causes 87x slowdown (0.11 vs ~1.0 tok/s). This means the model must fit entirely in `malloc`'d RAM.

---

## Onboarding Checklist

For each new SoC type joining the fleet:

### 1. Determine SoC

```bash
adb shell getprop ro.hardware.chipname   # e.g., "lahaina" = SD 888
adb shell cat /proc/cpuinfo | grep "CPU implementer\|CPU part"
```

### 2. Set build flags

Look up the SoC in the build flags table above. Key decisions:
- Does it have i8mm? → Add `+i8mm` to march
- Does it have SVE2? → Add `+sve2` to march (NOT on SD 8 Elite/Oryon)
- What `-mcpu` target? → Match the big core microarchitecture

### 3. Build the binary

```bash
# Edit scripts/build_cellswarm.sh or pass flags via environment
export SWARM_CFLAGS="-march=armv9-a+i8mm+sve2+dotprod+fp16 -mcpu=cortex-a710"
scripts/build_cellswarm.sh
```

### 4. Determine thread count and taskset

| SoC | Threads | Taskset | Reason |
|-----|---------|---------|--------|
| SD 870 | 4 | `f0` | 4 big A77 cores (cores 4-7) |
| SD 888 | 4 | `f0` | 3 big A78 + 1 X1 (cores 4-7) |
| SD 8 Gen 1 | 4 | `f0` | 3 big A710 + 1 X2 (cores 4-7) |
| SD 8+ Gen 1 | 4 | `f0` | Same layout as 8 Gen 1 |
| SD 8 Gen 2 | 4 | `f0` | 4 big cores (2x A715 + 2x A710) |
| SD 8 Gen 3 | 5 | `1f0` | 5 big A720 cores (cores 4-8) |
| SD 8 Elite | 6 | `3f0` | All 8 cores are big; use 6 for thermal |

**How to verify core layout:**
```bash
adb shell cat /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq
# Big cores have higher max freq; use those core numbers for taskset
```

### 5. Deploy and benchmark

```bash
# Deploy binary and model to phone
scripts/deploy_phones.sh
scripts/deploy_model.sh 1 --target --all

# Quick single-phone benchmark
scripts/bench_cellswarm_ethernet.sh 1
# or for USB phones:
scripts/bench_cellswarm_phoneonly.sh 1
```

### 6. Validate with calculator

```bash
# Get per-layer time from benchmark output, then estimate optimal fleet size
python3 scripts/phone_calculator.py \
  --layers 62 --model-size 18.6 --ram 5.0 \
  --per-layer-ms <MEASURED_VALUE>
```

---

## Multi-Device Fleet

### Same-SoC fleet (simplest)

All phones have the same SoC → one binary, one set of flags. This is our production setup (40x Z Fold3, all SD 888).

### Mixed-SoC fleet

Different SoC types require different binaries due to ISA differences (i8mm, SVE2):

**Option A: Per-SoC binaries (recommended)**

Build separate binaries for each SoC family:
```bash
# SD 888 (no i8mm)
SWARM_CFLAGS="-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a78" \
  scripts/build_cellswarm.sh
mv bin/cellswarm-worker bin/cellswarm-worker-sd888

# SD 8 Gen 1+ (i8mm + SVE2)
SWARM_CFLAGS="-march=armv9-a+i8mm+sve2+dotprod+fp16 -mcpu=cortex-a710" \
  scripts/build_cellswarm.sh
mv bin/cellswarm-worker bin/cellswarm-worker-sd8g1

# SD 8 Elite (i8mm, NO SVE2)
SWARM_CFLAGS="-march=armv8.7-a+i8mm+dotprod+fp16" \
  scripts/build_cellswarm.sh
mv bin/cellswarm-worker bin/cellswarm-worker-sd8elite
```

Deploy the correct binary to each phone based on its SoC.

**Option B: Lowest common denominator**

Build with `-march=armv8.2-a+dotprod+fp16` for maximum compatibility. This runs on ALL SoCs but misses i8mm/SVE2 speedups on newer phones. Only recommended for quick testing.

### Mixed fleet ring considerations

In a ring topology, the **slowest phone** determines the pipeline drain rate. If you mix SD 888 (12ms/layer) with SD 8 Gen 3 (6ms/layer) phones:

- Put faster phones at higher ranks (more layers per faster phone)
- Adjust layer distribution so each rank takes similar wall-clock time
- The calculator assumes uniform hardware; for mixed fleets, manually tune layer counts

Example: 10-phone ring with 5x SD 888 + 5x SD 8 Gen 3
```
SD 888 phones (12ms/layer): assign 4 layers each → 48ms compute
SD 8 Gen 3 phones (6ms/layer): assign 8 layers each → 48ms compute
Total: 5*4 + 5*8 = 60 layers (close enough for a 62-layer model)
```

This equalizes per-rank compute time and maximizes pipeline throughput.
