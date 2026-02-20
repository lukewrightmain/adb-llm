# 04 — Benchmark Results

All results: DeepSeek Coder 33B Instruct, Q4_K_M quantization (18.6 GiB, 62 layers), unless otherwise noted. Context 512 tokens, generation 64 tokens, temperature 0.8.

## Section A: llama.cpp RPC (Feb 14)

3 Samsung Galaxy Z Fold3 phones via USB tunnels, host coordination on coding server.

| Metric | Before Optimization | After Optimization | Change |
|--------|--------------------|--------------------|--------|
| Generation speed | 0.75 tok/s | 1.08 tok/s | **+44%** |
| Model load (first) | ~20 min | ~11 min | -45% |
| Model load (cached) | ~20 min | ~2 min | **-90%** |

Optimizations applied:
1. ARM dotprod+fp16 compilation flags
2. LZ4 compression for RPC traffic
3. swarm-rpc hash cache for model loading
4. graph_recompute cache bug fix (topology-only comparison)
5. Batched init_tensor for model loading

The graph_recompute fix was the largest single contributor — it eliminated ~1.8 MB of per-token serialization across 3 phones, replacing it with 3 x 4-byte "recompute" messages.

## Section B: Sequential Ring (Non-Speculative)

cellswarm ring topology, no speculative decoding, no pipeline parallelism.

| Configuration | tok/s | ms/token | Notes |
|--------------|-------|----------|-------|
| 3-phone USB | 1.07 | 931 | Q4_K_M, `--no-mmap`, `-t 4`, `taskset f0` |
| 4-phone Ethernet | 1.04 | 958 | Same flags, direct IP |
| 10-phone Ethernet | ~1.04 | ~960 | Throughput constant regardless of phone count |

The sequential ring processes one token at a time through all 62 layers. Adding phones reduces per-phone compute but doesn't reduce the total ring traversal time (communication overhead fills the gap). This is the fundamental limitation that pipeline parallelism addresses.

## Section C: Speculative Decoding + Pipeline Parallelism (Feb 18)

Ethernet phones, direct IP, `--no-mmap`, `-t 4`, `taskset f0`, `--draft-max 8`.

Draft model: DeepSeek Coder 1.3B Instruct Q4_K_M (~832 MiB), running on rank 0 phone.

### Scaling Table

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

### Per-Rank Timing (Steady State)

| Metric | 8-phone (8 layers) | 10-phone (6 layers) | 15-phone (4 layers) |
|--------|-------------------|---------------------|---------------------|
| Recv wait | 0-30ms | 0-25ms | 0-20ms |
| Compute | 92-102ms | 70-80ms | 48-55ms |
| Send | ~0ms | ~0ms | ~0ms |

### Cycle Breakdown (10 Phones)

| Phase | Duration | Description |
|-------|----------|-------------|
| KV management | ~0ms | KV cache bookkeeping |
| Draft resync | ~38ms | Sync draft model state with target |
| Draft loop | ~305ms | Generate 8 draft tokens with 1.3B model |
| Verify KV ops | ~0ms | Prepare KV cache for verification |
| Verify decode | ~1,840ms | Pipeline 9 tokens through 10-phone ring |
| **Total cycle** | **~2,145ms** | **Produces ~6 accepted tokens** |

### Pipeline Timing Detail (10 Phones)

```
Phase 1 (Send):     Rank 0 sends 9 embedding batches     ~1ms
First result:       Full ring round-trip                   ~1,000ms
Subsequent results: Pipeline drain                         every 20-30ms
Total pipeline:     All 9 tokens verified                  ~1,840ms
```

## Section D: Scaling Analysis

### Why 10 Phones is Optimal

At 10 phones, each phone processes ~6 layers:
- Per-phone compute: ~72ms
- Per-hop communication: ~3ms
- 10 hops: ~30ms communication overhead
- Pipeline overlap is effective: first token after ~1,000ms, then every ~80ms

The trade-off:

| Factor | More phones | Fewer phones |
|--------|------------|--------------|
| Layers per phone | Fewer (faster compute) | More (slower compute) |
| Ring hops | More (more comm overhead) | Fewer (less comm overhead) |
| Pipeline drain | Faster (less per-rank work) | Slower (more per-rank work) |
| Pipeline fill | Same (full ring latency) | Same |

Beyond 10 phones, communication overhead (hops x 3ms) grows faster than compute savings shrink. At 20 phones, each phone has only 3 layers (~36ms compute), but 20 hops add ~60ms and the pipeline fill/drain ratio worsens.

### Acceptance Rate vs Phone Count

Acceptance rate drops with more phones:

| Phones | Acceptance % | Avg Accepted Tokens |
|--------|-------------|---------------------|
| 4 | 69.3% | ~5.5 |
| 8 | 79.2% | ~6.3 |
| 10 | 77.8% | ~6.2 |
| 15 | 58.3% | ~4.7 |
| 20 | 45.5% | ~3.6 |

The 8-10 phone range has the highest acceptance, likely because the pipeline timing allows draft and target to stay well-synchronized. At higher phone counts, the longer verification time causes the draft model's predictions to become stale.

### Improvement Over Baseline

| Configuration | tok/s | Multiplier vs non-spec |
|--------------|-------|------------------------|
| Non-speculative ring (any # phones) | 1.04 | 1.0x |
| 10-phone speculative + pipeline | 3.345 | **3.2x** |

### Draft Model Overhead

The draft loop is consistently ~300ms regardless of phone count (it runs entirely on rank 0's phone). This is the "floor" cost of each speculative cycle — even with infinite pipeline speed, the maximum throughput would be 8 tokens / 300ms = 26.7 tok/s (if all 8 drafted tokens were always accepted).

## Log Files

All benchmark logs saved to `/tmp/`:
- `/tmp/cellswarm-ethernet-{N}phone-spec-d8-Q4_K_M.log` for N in {4, 6, 8, 10, 12, 15, 20}
