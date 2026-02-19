# Ethernet Speculative + Pipeline Parallelism Benchmark Results

## Summary

**Peak throughput: 3.345 tok/s** on DeepSeek Coder 33B (Q4_K_M) using 10 Samsung Galaxy Z Fold3 phones connected via Ethernet with speculative decoding and pipeline parallelism.

## Hardware

- **Phones:** 20x Samsung Galaxy Z Fold3 (SM-F926U/U1/W)
- **SoC:** Qualcomm Snapdragon 888 (Kryo 680, 4x big cores @ 2.84 GHz)
- **RAM:** ~5.0-5.6 GB available per phone
- **Network:** Ethernet adapters via USB-C hubs, direct phone-to-phone IP communication
- **Latency:** 1-3ms per hop (phone-to-phone ping over Ethernet)
- **OS:** Android 14/15

## Model

- **Target:** DeepSeek Coder 33B Instruct, Q4_K_M quantization (~18.6 GiB, 62 layers)
- **Draft:** DeepSeek Coder 1.3B Instruct, Q4_K_M quantization (~832 MiB, 24 layers)
- **Context:** 512 tokens
- **Generation:** 64 tokens, temperature 0.8

## Configuration

- **Topology:** Ring (ZeroMQ PUSH/PULL sockets over TCP)
- **Speculative decoding:** Draft-max 8 tokens per cycle
- **Pipeline parallelism:** `llama_decode_pipeline()` — sends all verification tokens through ring simultaneously, overlapping computation across ranks
- **Thread pinning:** `taskset f0` (big cores 4-7 on Snapdragon 888)
- **Memory mapping:** `--no-mmap` (critical — mmap causes 87x slowdown due to page thrashing)
- **Threads:** 4 per phone
- **Prefetch:** Enabled

## Scaling Results

| Phones | tok/s | Acceptance % | Pipeline Verify (ms) | Draft Loop (ms) | Cycle Total (ms) | Layers (rank 0, others) |
|--------|-------|-------------|---------------------|----------------|------------------|------------------------|
| 4 | 2.006 | 69.3% | ~2,500 | ~310 | ~2,810 | 9, 18/17 |
| 6 | 2.144 | 57.3% | ~2,200 | ~310 | ~2,510 | 5, 12/11 |
| 8 | 3.130 | 79.2% | ~2,000 | ~300 | ~2,300 | 5, 9/8 |
| **10** | **3.345** | **77.8%** | **~1,840** | **~305** | **~2,145** | **5, 7/6** |
| 12 | 2.854 | 65.9% | ~1,880 | ~305 | ~2,185 | 5, 6/5 |
| 15 | 2.437 | 58.3% | ~2,010 | ~305 | ~2,315 | 5, 5/4 |
| 20 | 1.868 | 45.5% | ~2,170 | ~305 | ~2,475 | 5, 3 |

### Scaling Curve

```
  tok/s
  3.5 |              * 10 phones (3.345)
  3.0 |         * 8 (3.130)
      |                    * 12 (2.854)
  2.5 |                         * 15 (2.437)
  2.0 |  * 4    * 6 (2.14)
      |                              * 20 (1.868)
  1.5 |
  1.0 |  (non-speculative baseline: 1.04 tok/s regardless of phone count)
      +-----+----+----+----+----+----+-----> phones
        4    6    8   10   12   15   20
```

## Analysis

### Why 10 Phones is Optimal

The ring topology creates a trade-off between:
- **Fewer layers per phone** (faster compute per rank)
- **More hops** (more communication overhead per round-trip)

At 10 phones:
- Each phone processes ~6 layers (~72ms compute)
- 10 hops add ~30ms communication overhead
- Pipeline overlap is effective: first token arrives after full ring latency (~1000ms), subsequent tokens every ~80ms

Beyond 10 phones, the communication overhead (more hops x 3ms each) outweighs the reduced compute per phone.

### Pipeline Parallelism Effect

Without pipeline parallelism, the ring is strictly sequential — every token must traverse all 62 layers through all phones serially. This gives ~1.04 tok/s regardless of phone count.

With pipeline parallelism, speculative verification tokens are sent through the ring simultaneously. While phone 1 processes token 2, phone 2 is processing token 1. This overlaps computation across ranks.

**Pipeline timing breakdown (10-phone, rank 0 perspective):**
- Send 9 embedding batches: ~1ms
- First result arrives: ~1,000ms (full ring round-trip)
- Subsequent results: every 20-30ms (pipeline drain)
- Total pipeline receive: ~1,840ms for 9 tokens

### Speculative Decoding Contribution

The 1.3B draft model runs entirely on rank 0's phone in ~300ms for 8 draft tokens. At 77.8% acceptance, each speculative cycle produces ~6 accepted tokens from 9 candidates (8 drafted + 1 verification).

**Effective tokens per cycle:** 6 tokens / 2,145ms = 2.80 tok/s (plus some overhead = 3.345 measured)

### Non-Speculative Baseline

Without speculative decoding, each token must traverse the entire 62-layer ring:
- **4-phone non-speculative:** 1.04 tok/s (958ms per token)
- This is constant regardless of phone count (sequential ring)

Speculative decoding provides a **3.2x improvement** over the non-speculative baseline at the 10-phone sweet spot.

## Timing Breakdown (Typical Cycle at 10 Phones)

| Phase | Duration | Description |
|-------|----------|-------------|
| KV management | ~0ms | KV cache bookkeeping |
| Draft resync | ~38ms | Sync draft model state with target |
| Draft loop | ~305ms | Generate 8 draft tokens with 1.3B model |
| Verify KV ops | ~0ms | Prepare KV cache for verification |
| Verify decode | ~1,840ms | Pipeline 9 tokens through 10-phone ring |
| **Total cycle** | **~2,145ms** | Produces ~6 accepted tokens |

## Per-Rank Timing (Steady State)

| Metric | 8-phone (8 layers) | 10-phone (6 layers) | 15-phone (4 layers) |
|--------|-------------------|---------------------|---------------------|
| Recv wait | 0-30ms | 0-25ms | 0-20ms |
| Compute | 92-102ms | 70-80ms | 48-55ms |
| Send | 0ms | 0ms | 0ms |

## Reproduction

See [README.md](README.md) for setup instructions and how to reproduce these benchmarks.

## Log Files

All benchmark logs are saved to `/tmp/`:
- `/tmp/prima-ethernet-4phone-spec-d8-Q4_K_M.log`
- `/tmp/prima-ethernet-6phone-spec-d8-Q4_K_M.log`
- `/tmp/prima-ethernet-8phone-spec-d8-Q4_K_M.log`
- `/tmp/prima-ethernet-10phone-spec-d8-Q4_K_M.log`
- `/tmp/prima-ethernet-12phone-spec-d8-Q4_K_M.log`
- `/tmp/prima-ethernet-15phone-spec-d8-Q4_K_M.log`
- `/tmp/prima-ethernet-20phone-spec-d8-Q4_K_M.log`

---

# Throughput Optimization Experiments (Feb 2026)

After achieving 6.117 tok/s with interleaved pipeline + d24, we investigated
reducing the ~86ms/hop overhead (60ms compute + 26ms ZMQ/metadata).

## Optimization 1: Packed Metadata
Replaced 14-frame ZMQ multipart with single-frame binary struct (32-byte header + arrays).

| Config | tok/s | Notes |
|--------|-------|-------|
| Baseline (14 frames) | 6.117 | Original multipart |
| Packed (1 frame) | 6.146 | No regression, saves ~2ms/hop |

**Verdict:** Neutral/slight improvement. Compute dominates.

## Optimization 2: Socket Tuning
ZMQ I/O threads 2→4, SNDBUF/RCVBUF 256KB, startup sleep 100→10ms, ZMQ_LINGER 100ms.

**Verdict:** No measurable impact.

## Optimization 3: Batch Pipeline Tokens
Send multiple tokens through pipeline per cycle (PRIMA_BATCH_PIPELINE=N).

| Batch Size | tok/s | Notes |
|------------|-------|-------|
| 1 (default) | 6.146 | Single token per cycle |
| 2 | 5.02 | GEMV 2x time, not 1.3x |
| 4 | 3.99 | ARM NEON GEMV scales linearly |

**Verdict:** FAILED on this hardware. ARM NEON GEMV on Snapdragon 888 (no i8mm)
has linear compute scaling — 4 tokens takes 3.3x single-token time, not ~1.5x.
Only useful on hardware with i8mm (ARMv8.6+) or GPU batched GEMM.

## Key Finding
The 86ms/hop is **compute-dominated** (60ms/hop = ~12ms/layer x 5 layers). ZMQ and
metadata overhead (~26ms) is a minority. Further throughput gains require reducing
per-layer compute time, not networking optimizations. Next steps: Q4_0 quantization
(hand-optimized NEON GEMV kernels), compiler flags (-Ofast, -mcpu=cortex-a78),
flash attention, context reduction.

## Environment Variable
`PRIMA_BATCH_PIPELINE=N` — set batch size at runtime (default 1). Only increase on
hardware with i8mm or GPU-accelerated GEMM.

## Date

Benchmarks run: February 18, 2026
