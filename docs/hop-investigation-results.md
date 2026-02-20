# Hop Investigation Results

**Date:** 2026-02-19
**Branch:** `zmq-hop-investigation`
**Config:** 4 phones (ethernet), DeepSeek 33B Q4_K_M, speculative d24, seed 100

---

## TL;DR

**ZMQ is NOT the bottleneck.** Per-hop ZMQ overhead is 20-50 microseconds (0.02-0.05ms), not 26ms. The "26ms overhead" from our pipeline model was a calculation artifact — it included compute variance and pipeline scheduling, not actual network/ZMQ costs.

---

## Raw TCP Baseline (phone-to-phone, 28.7KB payload)

| Metric | Mean | p50 | p95 | p99 |
|--------|------|-----|-----|-----|
| Send (write syscall) | 623 us | 615 us | 746 us | 922 us |
| Recv (read syscall) | 3,723 us | 3,667 us | 3,941 us | 5,862 us |
| Full RTT | 3,884 us | 3,836 us | 4,099 us | 6,124 us |

Consistent across phone pairs (tested 12→13 and 17→19).

Payload size scaling:
| Payload | RTT (mean) |
|---------|------------|
| 1 KB | 1.1 ms |
| 28.7 KB | 3.9 ms |
| 57.4 KB | 6.3 ms |

---

## ZMQ Hop Profile (PRIMA_HOP_PROFILE=1)

### Send side (all ranks, 28.7KB activation)

| Component | Time |
|-----------|------|
| msg_alloc (zmq::message_t constructor) | 2-4 us |
| pack (memcpy header + tensor) | 1-2 us |
| zmq_send (handoff to I/O thread) | 17-32 us |
| **Total SEND** | **20-50 us** |

### Recv side

| Scenario | zmq_recv time |
|----------|---------------|
| Data already buffered (pipeline back-to-back) | **1-5 us** |
| Waiting for previous rank compute | 10,000 - 530,000 us |

The large recv times are NOT ZMQ overhead — they're time waiting for the previous rank to finish computing its layers and send the result. When data is already buffered, ZMQ delivers it in 1-5 microseconds.

### Per-rank steady-state (28.7KB tokens in pipeline)

| Rank | Avg SEND | Avg RECV (buffered) | Avg RECV (waiting) |
|------|----------|--------------------|--------------------|
| 0 (host) | 5-30 us | N/A (varies) | 130-175 ms (full ring) |
| 1 | 20-27 us | 1-5 us | 2.9-3.4 ms (first hop) |
| 2 | 26-36 us | varies | 2.5-21.6 ms |
| 3 | 24-35 us | varies | 0-18.7 ms |

---

## Analysis

### Original hypothesis (WRONG)

> "~22ms is pure software overhead in ZMQ"
> - ZMQ message construction: 1-2ms ← **ACTUAL: 2-4 us (500x less)**
> - ZMQ I/O thread handoff: 2-4ms ← **ACTUAL: 17-32 us (100x less)**
> - ZMQ poll/wake latency: 3-5ms ← **ACTUAL: 1-5 us when buffered**
> - Buffer copies: 1-2ms ← **ACTUAL: 1-2 us**

### What the "26ms overhead" actually is

The 26ms per-hop overhead in our pipeline timing model was derived as:
```
measured_hop = total_pipeline_time / (n_phones * n_tokens) - compute_time
            = 86ms - 60ms = 26ms
```

But this calculation assumes uniform compute time and perfect pipelining. In reality:
1. **Network transit** is ~3.9ms per hop (confirmed by raw TCP)
2. **Compute variance** between tokens and layers causes pipeline bubbles
3. **Pipeline drain** at the end of each cycle has longer waits
4. **Rank 0 overhead** (sampling, draft generation) creates gaps

The actual per-hop overhead breakdown:
- Network transit: ~3.9ms
- ZMQ send/recv: ~0.05ms
- Pipeline scheduling gaps: ~22ms (the real bottleneck)

### Pipeline scheduling is the bottleneck

The pipeline scheduling overhead comes from:
1. **Layer count imbalance**: With 4 phones and layers [5,19,19,19], rank 0 finishes in ~60ms while others take ~228ms. The pipeline can't drain faster than the slowest rank.
2. **Interleaving gaps**: Draft tokens are generated one at a time (~38ms each on rank 0). Each enters the pipeline with a gap.
3. **Resync overhead**: After each cycle, all ranks synchronize KV cache state.

---

## Implications

### Replacing ZMQ with raw TCP: NOT WORTH IT

ZMQ adds <0.1ms per hop. Raw TCP would add ~3.9ms per hop (same network transit). Replacing ZMQ would make things **slower** because we'd lose the I/O thread buffering that gives us 1-5us recv when data is ready.

### What would actually help

1. **Better pipeline scheduling** — Reduce gaps between tokens entering the ring
2. **More balanced layer distribution** — Currently [5,19,19,19] for spec; reducing compute variance between ranks reduces pipeline bubbles
3. **Reduce compute time** — The ~228ms per rank (19 layers × 12ms) dominates everything. Faster SoCs (i8mm) or fewer layers per rank (more phones) directly reduces cycle time
4. **Reduce network RTT** — The 3.9ms per hop is hardware-limited (ethernet adapter + Android TCP stack). Could investigate kernel bypass or UDP for marginal gains.

### Revised pipeline timing model

The 86ms/hop figure should be decomposed as:
- Rank compute: 60ms (5 layers × 12ms) to 228ms (19 layers × 12ms)
- Network transit: ~4ms
- ZMQ overhead: ~0.05ms (negligible)
- Pipeline bubble: varies (0ms when perfectly pipelined, up to 168ms at imbalanced ranks)

For the 12-phone production config [1,6,6,6,6,6,6,5,5,5,5,5]:
- Max rank compute: 6 × 12 = 72ms
- Network transit: ~4ms
- Pipeline hop: ~76ms
- This matches our measured ~86ms (the extra 10ms is likely from pipeline scheduling gaps and compute variance)

---

## Files

- `docs/hop-investigation.md` — Original investigation plan
- `docs/hop-investigation-results.md` — This document
- `benchmarks/tcp_hop_bench.c` — Raw TCP benchmark tool
- `vendor/prima.cpp/src/llama.cpp` — PRIMA_HOP_PROFILE instrumentation
