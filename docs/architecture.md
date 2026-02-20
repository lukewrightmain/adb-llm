# Architecture Comparison: Stock llama.cpp → cellswarm → Our Fork

This document describes the architectural evolution from stock llama.cpp through cellswarm to our production fork, which runs DeepSeek 33B Q4_K_M at 6.1 tok/s across 12 Samsung Galaxy Z Fold3 phones.

---

## 1. Stock llama.cpp

**Repository:** [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp)

Stock llama.cpp is a single-machine inference engine for GGUF-format LLMs. Key characteristics:

- **Single process**: One swarm-server or cellswarm-cli binary runs on one machine
- **Prompt eval (prefill)**: Processes the entire prompt in a single batched forward pass
- **Token decode**: Generates tokens one at a time, each requiring a full model forward pass
- **Backend support**: CPU (NEON/AVX/AVX-512), CUDA, Metal, Vulkan, OpenCL, SYCL
- **Memory mapping**: Uses mmap by default to load model weights from disk
- **KV cache**: Single contiguous allocation, grows with context length
- **No distributed inference**: The entire model must fit on one machine (CPU RAM + VRAM)

### Inference flow

```
Prompt → [Tokenize] → [Prompt Eval: all layers, full batch] → [KV Cache]
                                                                   ↓
Token ← [Detokenize] ← [Sample] ← [Decode: all layers, 1 token] ←┘ (loop)
```

### Speculative decoding (stock)

Stock llama.cpp supports speculative decoding with a draft model:
1. Draft model generates N candidate tokens autoregressively
2. Target model verifies all N tokens in a single batched forward pass
3. Accept tokens up to the first rejection, plus one bonus token
4. Both models run on the same machine

---

## 2. cellswarm (Base Fork)

**Origin:** Fork of llama.cpp adding ZMQ-based distributed inference over a ring topology.

cellswarm splits the model across multiple devices connected in a ring:

- **ZMQ ring topology**: Each rank connects to the next via ZMQ PUSH/PULL sockets
- **Layer splitting**: Model layers are divided across ranks (e.g., rank 0: layers 0-19, rank 1: layers 20-39, rank 2: layers 40-61)
- **Sequential ring decode**: For each token, activations flow around the entire ring
- **Host/Worker architecture**: Host (rank 0) runs the sampler; workers forward activations
- **Broadcast startup**: Rank 0 broadcasts model metadata and KV cache config to all ranks

### Ring topology

```
┌─────────┐    ┌─────────┐    ┌─────────┐
│ Rank 0  │───→│ Rank 1  │───→│ Rank 2  │
│ (Host)  │    │(Worker) │    │(Worker) │
│ L0-L19  │←───│ L20-L39 │←───│ L40-L61 │
└─────────┘    └─────────┘    └─────────┘
```

### Limitations of stock cellswarm

1. **Sequential ring**: Each token traverses the full ring before sampling — latency = sum of all ranks
2. **No pipeline parallelism**: The ring is idle while waiting for the previous token to complete
3. **ZMQ overhead**: Each message uses 14 separate ZMQ frames (metadata, tensor data, etc.)
4. **No speculative decoding**: Only autoregressive single-token decode
5. **No Android optimizations**: Assumes Linux/macOS desktop environment

---

## 3. Our Modifications (Production Fork)

Our fork adds four major systems on top of cellswarm:

### 3.1 Pipeline Parallelism

**Key file:** `vendor/cellswarm/src/llama.cpp` — `llama_decode_pipeline()`

Stock cellswarm processes one token at a time through the ring. Our pipeline parallelism overlaps multiple tokens in flight simultaneously:

```
Time →
Rank 0: [T1 compute] [T2 compute] [T3 compute] ...
Rank 1:              [T1 compute] [T2 compute] [T3 compute] ...
Rank 2:                           [T1 compute] [T2 compute] ...
```

Implementation:
- **Phase 1 (send_all)**: Split a verification batch into single-token batches. Send each token into the ring as soon as the previous rank finishes it, without waiting for the full ring traversal.
- **Phase 2 (recv_all)**: Collect completed tokens as they exit the ring. The first token arrives after a full ring traversal; subsequent tokens arrive at per-rank intervals.
- **Phase 3 (resync)**: Synchronize KV cache state across all ranks after the verification batch completes.

Pipeline state is managed via `llama_pipeline_state` struct in llama.cpp, tracking in-flight tokens and completion status per rank.

**Declared in:** `vendor/cellswarm/include/llama.h`
**Called from:** `vendor/cellswarm/common/speculative.cpp`

### 3.2 Interleaved Speculative Decoding

**Key file:** `vendor/cellswarm/common/speculative.cpp`

Stock speculative decoding is serial: draft all tokens, then verify all tokens. Our interleaved approach overlaps drafting with pipeline verification:

```
Standard speculative:
[Draft N tokens: ~900ms] → [Verify N tokens through ring: ~2150ms] → [Resync: ~37ms]

Interleaved speculative:
[Draft token 1] → send to ring immediately
[Draft token 2] → send to ring (token 1 is already at rank 1)
[Draft token 3] → send to ring (token 1 at rank 2, token 2 at rank 1)
...
[Draft token N] → by now, early tokens are completing the ring
[Receive remaining tokens from ring]
[Resync]
```

This hides ~900ms of draft time behind the pipeline, reducing effective cycle time from ~3950ms to ~3090ms.

### 3.3 Packed Metadata (Single-Frame ZMQ)

**Key file:** `vendor/cellswarm/src/llama.cpp` — `ring_msg_header` struct

Stock cellswarm sends 14 separate ZMQ frames per ring message (token ID, layer index, batch size, tensor dimensions, etc.). Our packed format serializes all metadata into a single binary header prepended to the tensor data:

```c
struct ring_msg_header {
    uint32_t magic;          // 0x50524D41 ("PRMA")
    uint32_t msg_type;
    uint32_t token_id;
    uint32_t layer_idx;
    uint32_t batch_size;
    uint32_t n_embd;
    uint32_t seq_id;
    uint32_t flags;
    // ... total 56 bytes
};
// Followed immediately by tensor data in the same ZMQ frame
```

**Impact:** Pipeline recv time dropped from 3080ms to 2260ms (+26%) in same-session A/B testing. The reduction comes from fewer ZMQ syscalls and better memory locality.

### 3.4 Android/ARM Optimizations

Optimizations specific to running on Android phones with Snapdragon 888 (Cortex-A78):

| Optimization | Detail |
|---|---|
| **`--no-mmap`** | Critical: mmap page thrashing causes 87x slowdown (0.11 vs 1.0 tok/s). We always use `--no-mmap` to force `malloc+read`. |
| **Thread pinning** | `taskset f0` pins to cores 4-7 (big Cortex-A78 cores). Little A55 cores drag down parallel NEON GEMV. |
| **Thread count** | `-t 4` is optimal. Matches the 4 big cores. `-t 3` is slower GEMV, `-t 8` includes slow A55 cores. |
| **Build flags** | `-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a78 -Ofast -fno-finite-math-only --strip-all` |
| **No i8mm** | Snapdragon 888 is ARMv8.4, lacks i8mm (SMMLA). Building with `+i8mm` causes SIGILL. |
| **Binary size** | `-Ofast` + `--strip-all` reduces binary from 40MB to 3.1MB. |
| **Q4_K_M over Q4_0** | Q4_0's repacked GEMV kernels require i8mm. Q4_K_M performs better on these devices. |

---

## 4. Feature Comparison

| Feature | llama.cpp | cellswarm | Our Fork |
|---|:---:|:---:|:---:|
| Single-machine inference | Yes | Yes | Yes |
| Distributed inference | No | Yes (ZMQ ring) | Yes (ZMQ ring) |
| Pipeline parallelism | N/A | No | **Yes** |
| Speculative decoding | Yes (single machine) | No | **Yes (distributed)** |
| Interleaved spec+pipeline | N/A | N/A | **Yes** |
| Packed ZMQ messages | N/A | No (14 frames) | **Yes (1 frame)** |
| Android ARM optimizations | Partial | No | **Yes** |
| Phone-to-phone direct ring | N/A | No | **Yes** |
| `--no-mmap` for phones | Available | Available | **Required + enforced** |
| Thread pinning (taskset) | Manual | Manual | **Scripted** |
| Batch pipeline decode | N/A | N/A | **Configurable (SWARM_BATCH_PIPELINE)** |

---

## 5. Key Code Locations

| Component | File | Function/Struct |
|---|---|---|
| Pipeline decode | `vendor/cellswarm/src/llama.cpp` | `llama_decode_pipeline()` |
| Pipeline state | `vendor/cellswarm/src/llama.cpp` | `struct llama_pipeline_state` |
| Pipeline declaration | `vendor/cellswarm/include/llama.h` | `llama_decode_pipeline()` |
| Interleaved speculative | `vendor/cellswarm/common/speculative.cpp` | Main decode loop |
| Packed metadata header | `vendor/cellswarm/src/llama.cpp` | `struct ring_msg_header` |
| ZMQ ring sockets | `vendor/cellswarm/src/llama.cpp` | ~line 20492 |
| Ring broadcast startup | `vendor/cellswarm/src/llama.cpp` | `bcast_startup_args()` |
| Build script (ARM64) | `scripts/build_cellswarm.sh` | CFLAGS, ENABLE_VULKAN |
| Benchmark scripts | `scripts/bench_cellswarm_ethernet.sh` | Direct IP ring benchmark |
| Model deployment | `scripts/deploy_model.sh` | Push GGUF to phones |

---

## 6. Performance Summary

### Sequential ring (no pipeline, no speculative)

| Config | tok/s |
|---|---|
| 3 phones, Q4_K_M, --no-mmap | 1.07 |
| 4 phones, Q4_K_M, ethernet | 1.04 |

### Speculative + pipeline (our fork)

| Config | tok/s | Speedup |
|---|---|---|
| 10 phones, d8, baseline pipeline | 3.345 | 3.1x |
| 10 phones, d24, interleaved + packed | 5.537 | 5.2x |
| 12 phones, d24, interleaved + packed | **6.117** | **5.7x** |

### Per-cycle timing breakdown (12 phones, d24)

| Phase | Time |
|---|---|
| Draft generation + Phase 1 (interleaved) | ~900ms |
| Pipeline recv_all (Phase 2) | ~2150ms |
| Resync (Phase 3) | ~37ms |
| **Total cycle** | **~3090ms** |
| Accepted tokens per cycle | ~18.6 (24 × 0.745 + 1) |
| **Throughput** | **~6.0 tok/s** |

---

## 7. Diagram: Full System Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │              Host (Coding Server)             │
                    │  - Builds ARM64 binaries (cellswarm-worker-spec) │
                    │  - Deploys via ADB / direct IP               │
                    │  - NOT used for inference compute             │
                    └──────────────────────────────────────────────┘
                                        │ deploy
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
            ┌──────────────┐  ┌──────────────┐    ┌──────────────┐
            │   Phone 0    │  │   Phone 1    │    │   Phone N    │
            │  (Rank 0)    │  │  (Rank 1)    │    │  (Rank N)    │
            │              │  │              │    │              │
            │ Target model │  │ Target model │    │ Target model │
            │ (layers 0-k) │  │ (layers k-m) │...│ (layers x-61)│
            │              │  │              │    │              │
            │ Draft model  │  │              │    │              │
            │ (all layers) │  │              │    │              │
            │              │  │              │    │              │
            │   Sampler    │  │              │    │              │
            └──────┬───────┘  └──────┬───────┘    └──────┬───────┘
                   │    ZMQ PUSH     │                   │
                   └────────────────→│                   │
                                     └──────────→  ...  →│
                   ←─────────────────────────────────────┘
                          ZMQ PULL (ring return)
```
