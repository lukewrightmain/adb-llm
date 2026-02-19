# Distributed LLM Inference on ARM Phone Clusters

## Abstract

We run DeepSeek Coder 33B (Q4_K_M, 18.6 GiB, 62 layers) across a cluster of Samsung Galaxy Z Fold3 phones (Snapdragon 888, ARM CPU-only) using prima.cpp with speculative decoding and pipeline parallelism over Ethernet. Our peak result is **3.345 tokens/second on 10 phones**, achieved February 18, 2026.

This is, to our knowledge, the fastest published result for 33B-class LLM inference on consumer ARM devices without GPU acceleration. It outperforms published Raspberry Pi cluster benchmarks by approximately 4x at comparable model sizes.

## Key Findings

1. **`--no-mmap` is the single most impactful optimization** — Android's mmap page thrashing causes an 87x slowdown (0.011 vs 1.04 tok/s). Explicit memory loading eliminates this entirely.
2. **Pipeline parallelism is essential for multi-phone scaling.** Without it, ring topology throughput is constant (~1.04 tok/s) regardless of phone count. With it, throughput scales sub-linearly up to 10 phones.
3. **10 phones is the sweet spot.** Beyond 10, communication overhead (3ms/hop) outweighs the reduced per-phone compute. At 20 phones, throughput drops to 1.87 tok/s.
4. **Speculative decoding provides a 3.2x improvement** over the non-speculative baseline, with a lightweight 1.3B draft model running entirely on the rank-0 phone.
5. **GPU acceleration on Snapdragon 888 (Adreno 660) is non-functional** — OpenCL kernel compiler crashes, Vulkan hits DeviceLost. Only Adreno 750+ (Snapdragon 8 Gen 3) has viable GPU compute.

## Quick Comparison

| Project | Model | Devices | GPU? | Network | tok/s |
|---------|-------|---------|------|---------|-------|
| **Ours (prima.cpp)** | **33B Q4_K_M** | **10 phones** | **No** | **Ethernet** | **3.345** |
| prima.cpp official | 32B Q4_K_S | Mixed ARM+x86+GPU | Yes | InfiniBand | ~11.2 |
| RPi CM5 cluster | 70B Q4_0 | 10 RPi boards | No | Ethernet | ~0.85 |
| llama.cpp RPC (ours) | 33B Q4_K_M | 3 phones + host | No | USB tunnels | 1.08 |
| Petals | 70B | GPU cluster | Yes | Internet | ~6.0 |
| PowerInfer-2 | 7B | 1 phone | GPU | N/A | ~11.0 |

## Table of Contents

1. [Hardware Platform](01-hardware-platform.md) — Phone specs, fleet inventory, network setup
2. [Methods Survey](02-methods-survey.md) — All known ARM inference methods and frameworks
3. [Our Approach](03-our-approach.md) — Architecture evolution from RPC to pipeline ring
4. [Benchmark Results](04-benchmark-results.md) — All our measured numbers
5. [External Benchmarks](05-external-benchmarks.md) — Other projects' claimed numbers
6. [Comparison Analysis](06-comparison-analysis.md) — Side-by-side tables, scaling, cost analysis
7. [Lessons Learned](07-lessons-learned.md) — Critical discoveries and pitfalls
8. [Future Directions](08-future-directions.md) — Next steps and unexplored approaches
9. [References](09-references.md) — All URLs, papers, and repositories

## Reproduction

See the [main project README](../README.md) for setup and reproduction instructions. The benchmark script:

```bash
./scripts/bench_prima_ethernet.sh 10 --spec
```

## Date

Research compiled: February 18, 2026
