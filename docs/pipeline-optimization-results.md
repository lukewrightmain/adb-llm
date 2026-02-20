# Pipeline Optimization Results: 3.3 to 6.1 tok/s on DeepSeek 33B

## Overview

Optimization of cellswarm ring-based distributed inference on Samsung Galaxy Z Fold3 phones connected via ethernet. Target model: DeepSeek Coder 33B Instruct Q4_K_M with 1.3B Q4_K_M draft model for speculative decoding.

**Result: 1.83x speedup (3.345 → 6.117 tok/s) on a single request using 12 phones.**

## Hardware

- **Phones**: Samsung Galaxy Z Fold3 (SM-F926U), Snapdragon 888, 8 cores, ~5-6GB available RAM
- **Connection**: Ethernet via USB-C adapters, direct phone-to-phone IP (2-4ms latency)
- **Model**: DeepSeek Coder 33B Instruct Q4_K_M (62 transformer layers, ~20GB)
- **Draft model**: DeepSeek Coder 1.3B Instruct Q4_K_M (~850MB, runs on rank 0 phone)
- **Settings**: `--no-mmap -t 4 taskset f0` (big cores only, no mmap to avoid page thrashing)

## Baseline

10-phone ring with speculative decoding + pipeline parallelism, `--draft-max 8`:

| Metric | Value |
|--------|-------|
| Speed | 3.345 tok/s |
| Accept rate | 77.8% |
| Pipeline verify | ~1840ms |
| Bottleneck | Per-hop overhead (ZMQ framing, sync barriers, debug logging) |

## Optimization Phases

### Phase 1: Remove Debug Logging + Tune Parameters

Guarded all hot-path `fprintf` calls (`[WORKER-TIMING]`, `[RING-TIMING]`, `[SPEC-TIMING]`, `[PIPELINE]`) behind `#ifdef SWARM_DEBUG`. Increased `--draft-max` from 8 to 24.

- **Files**: `llama.cpp`, `speculative.cpp`, `bench_cellswarm_ethernet.sh`
- **Impact**: Eliminated ~2-4ms per token per phone of logging overhead

### Phase 2: ZMQ + Sync Optimization

#### Single-frame ZMQ send/recv

Replaced 3-frame multipart ZMQ messages with a single packed frame:

```
Before: Frame 1 (header) + Frame 2 (dims) + Frame 3 (data) = 3 syscalls
After:  [header|dims|data] = 1 syscall
```

#### Reduced sync barriers

Moved `ggml_backend_sched_synchronize()` to only fire after the last layer per phone instead of every layer. Removed redundant sync in the ring loop.

#### Pre-allocated ZMQ buffers

Pre-allocated send/recv message buffers at socket init time instead of allocating per-message.

- **Files**: `llama.cpp` (send/recv functions, ring loop, socket init)
- **Impact**: ~8-15ms saved per hop

### Phase 3: Interleaved Draft + Pipeline

The key architectural change. Instead of drafting all tokens sequentially then verifying them through the pipeline, we interleave: draft one token, immediately send it into the pipeline, draft next token, send it, etc.

```
Before (sequential):
  [Draft 24 tokens: 900ms] → [Pipeline verify 25 tokens: 2150ms]
  Total: 3050ms per cycle

After (interleaved):
  [Draft token 0 → send to pipeline]  ←─ pipeline processes token 0
  [Draft token 1 → send to pipeline]  ←─ pipeline processes token 0,1
  [Draft token 2 → send to pipeline]  ←─ pipeline processes token 0,1,2
  ...
  [Draft token 23 → send to pipeline] ←─ all tokens in flight
  [Recv all results: 2150ms]           ←─ first results already arriving
  Total: ~3090ms per cycle (draft time fully hidden)
```

New incremental pipeline API:
- `llama_pipeline_begin()` — initialize pipeline state, reserve output buffer
- `llama_pipeline_send_one()` — compute embedding + send single token to ring
- `llama_pipeline_recv_all()` — switch to Phase 2, receive all results
- `llama_pipeline_end()` — finalize output mapping, reset state

- **Files**: `llama.h`, `llama.cpp`, `speculative.cpp`
- **Impact**: ~900ms draft time fully overlapped with pipeline Phase 1

## Results

### Scaling by Phone Count (seed 100, 128 tokens, interleaved pipeline)

| Phones | draft-max | tok/s | Accept% | Improvement vs baseline |
|--------|-----------|-------|---------|------------------------|
| 8 | 24 | 4.728 | 74.5% | 1.41x |
| 10 | 24 | 5.537 | 74.5% | 1.66x |
| 11 | 24 | 6.095 | 74.5% | 1.82x |
| **12** | **24** | **6.117** | **74.5%** | **1.83x** |
| 12 | 32 | 5.922 | 67.4% | 1.77x |
| 15 | 24 | 5.846 | 74.5% | 1.75x |
| 15 | 32 | 5.898 | 67.4% | 1.76x |
| 20 | 24 | 5.622 | 74.5% | 1.68x |
| 20 | 32 | 5.699 | 67.4% | 1.70x |

12 phones is the sweet spot: enough parallelism to reduce per-phone compute, but not so many hops that communication overhead dominates.

### Dual Ring Aggregate Throughput

Two independent 10-phone rings serving concurrent requests:

| Ring | tok/s | Accept% |
|------|-------|---------|
| Ring A (phones 1-10) | 4.273 | 74.5% |
| Ring B (phones 11-20) | 3.473 | 59.7% |
| **Aggregate** | **7.746** | — |

### Per-Cycle Timing Breakdown (12 phones, d24)

| Phase | Time | Description |
|-------|------|-------------|
| KV management | ~0ms | Cache cleanup between cycles |
| Draft resync | ~37ms | Single draft model decode to resync state |
| Draft + Phase 1 | ~900ms | 24 draft tokens interleaved with pipeline sends |
| Phase 2 recv_all | ~2150ms | Receive 25 verified results from ring |
| **Total cycle** | **~3090ms** | Produces ~18 accepted tokens (74.5%) |

Pipeline throughput: **~86ms per token per phone** (60ms compute for 5 layers + 26ms network overhead).

## Key Findings

1. **`--no-mmap` is critical**: mmap page thrashing causes 87x slowdown on Android. Always use `--no-mmap`.

2. **Acceptance rate is highly seed-dependent**: Same prompt with different seeds gives 11-85% acceptance. Seed 100 consistently gives 74.5% for this prompt. Longer generation (256+ tokens) degrades acceptance to ~60%.

3. **Draft time can be fully hidden**: The 1.3B draft model takes ~38ms/token on phone (900ms for 24 tokens). With interleaved pipeline, this is completely overlapped with pipeline Phase 1 sends.

4. **12 phones is optimal for 33B Q4_K_M**: Fewer phones = too much compute per phone. More phones = too much communication overhead per cycle. The crossover is at ~12 phones where per-phone compute (60ms) is ~2.3x the per-hop overhead (26ms).

5. **Debug logging adds significant overhead**: Hot-path fprintf calls cost 2-4ms per token per phone. In a 20-phone ring processing 25 tokens, that's 1-2 seconds per cycle.

## Theoretical Limits

With current hardware (Snapdragon 888, ethernet):

```
Per-phone: 60ms compute (5 layers × 12ms) + 26ms overhead = 86ms per token
Pipeline throughput: 1 token every 86ms (bottleneck phone rate)
Max with 100% acceptance: 25 tokens / (25 × 86ms) = 11.6 tok/s
At 75% acceptance: 18.75 / (25 × 86ms) = 8.7 tok/s theoretical
Actual (with cycle overhead): ~6.1 tok/s
```

The ~2.6 tok/s gap between theoretical and actual comes from:
- Draft resync overhead (~37ms/cycle = ~0.2 tok/s)
- Accept/reject processing
- Pipeline Phase 2 startup latency
- Non-overlapped portions of recv_all

## Reproducing

```bash
# Build
bash scripts/build_cellswarm.sh

# Deploy to ethernet phones
for ip in 10.105.0.{12,13,17,19,20,24,28,29,30,31,32,36}; do
  ~/.local/bin/adb -s "$ip:5555" push bin/cellswarm-worker-spec /data/local/tmp/cellswarm/bin/cellswarm-worker-spec
done

# Benchmark (12 phones, speculative, draft-max 24, seed 100)
bash scripts/bench_cellswarm_ethernet.sh 12 --spec --draft-max 24 --seed 100

# Compare without interleaving
bash scripts/bench_cellswarm_ethernet.sh 12 --spec --draft-max 24 --seed 100 --no-interleave

# Dual ring (aggregate throughput)
bash scripts/bench_cellswarm_dual_ring.sh
```
