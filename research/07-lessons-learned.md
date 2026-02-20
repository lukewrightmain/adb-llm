# 07 — Lessons Learned

Critical discoveries from the project, ranked by impact.

## 1. `--no-mmap` Eliminates 87x Slowdown

**Impact: 87x throughput improvement**
**Discovery date:** Feb 15, 2026

Android's memory management aggressively evicts mmap'd pages under memory pressure. When a 33B model (18.6 GiB) is mmap'd on a phone with ~5 GiB available RAM, the kernel constantly pages model weights in and out. Every layer access triggers page faults.

| Setting | tok/s | Behavior |
|---------|-------|----------|
| mmap (default) | ~0.011 | Constant page faulting, ~90s per token |
| `--no-mmap` | ~1.04 | Model loaded into allocated RAM at startup |

**Root cause:** llama.cpp defaults to mmap for large models. On desktop Linux with swap and abundant RAM, this is fine — the OS keeps hot pages resident. On Android with limited RAM and aggressive low-memory killer, mmap'd pages are evicted between layers of a single forward pass.

**Fix:** Always pass `--no-mmap` on Android. Model load takes ~2 minutes (explicit read) but inference runs at full speed.

**Broader lesson:** mmap behavior varies dramatically across OS/platform. Always benchmark with and without mmap on new platforms. The "Q4_K_M batch catastrophe" (where batch decode was supposedly 32x slower than Q4_0) was also mmap page thrashing, not a fundamental quantization issue. With `--no-mmap`, both Q4_K_M and Q4_0 batch decode at ~10ms/layer/token.

## 2. GPU Acceleration Fails on Adreno 660

**Impact: Eliminated GPU as an option entirely**
**Discovery date:** Feb 16, 2026

Two GPU compute paths were tested on Snapdragon 888's Adreno 660:

### OpenCL
- Adreno 660 driver (dated September 2023) crashes during OpenCL kernel compilation
- llama.cpp's OpenCL backend triggers driver bugs in the shader compiler
- No workaround — the driver is too old and Samsung/Qualcomm no longer update it

### Vulkan
- Vulkan 1.1 initializes successfully
- `vk::DeviceLostError` on compute shader dispatch
- Even `ngl=1` (single GPU layer) crashes
- BLAS-via-Vulkan benchmarked 2x slower than NEON CPU before crashing

**Minimum viable GPU:** Adreno 750+ (Snapdragon 8 Gen 3, released late 2023). Qualcomm QNN SDK also requires 8 Gen 3+.

**Lesson:** Don't assume GPU acceleration works on older mobile SoCs. The driver ecosystem is fragmented and unmaintained. Test empirically before planning around GPU compute.

## 3. No i8mm on Snapdragon 888 (SIGILL)

**Impact: Silent crash prevention**
**Discovery date:** Feb 14, 2026

Snapdragon 888 implements ARMv8.4-A. The `i8mm` (8-bit integer matrix multiply) extension requires ARMv8.6+. Building with `-march=armv8.2-a+dotprod+fp16+i8mm` compiles successfully but crashes with SIGILL (illegal instruction) at runtime.

The crash is particularly insidious because:
- Q4_0 quantization hits ARM GEMV/GEMM fast paths that use i8mm instructions
- The crash occurs inside compute kernels, not at startup
- No helpful error message — just SIGILL

**Fix:** Use `-march=armv8.2-a+dotprod+fp16` (no `+i8mm`). The dotprod extension provides the primary speedup for Q4_K_M; i8mm would additionally accelerate Q4_0 and Q8_0.

**Lesson:** Always verify ARM ISA support at runtime, or be explicit about target architecture. NEON feature detection can be done via `/proc/cpuinfo` or `getauxval(AT_HWCAP2)`.

## 4. Pipeline Parallelism is Essential for Scaling

**Impact: Transforms throughput from constant to scalable**
**Discovery date:** Feb 18, 2026

Without pipeline parallelism, the ring is strictly sequential — every token must traverse all 62 layers through all phones before the next token enters the ring. This gives exactly ~1.04 tok/s regardless of how many phones are in the ring (4, 10, or 20).

With pipeline parallelism (`llama_decode_pipeline()`), multiple tokens overlap in the ring. While phone 1 processes token 2, phone 2 processes token 1, etc.

| Mode | 4 phones | 10 phones | 20 phones |
|------|---------|-----------|-----------|
| Sequential ring | 1.04 tok/s | 1.04 tok/s | 1.04 tok/s |
| Pipeline + speculative | 2.006 tok/s | 3.345 tok/s | 1.868 tok/s |

**Implementation:** Split the verification batch into single-token batches. Phase 1 sends all tokens into the ring. Phase 2 receives all completed tokens. The pipeline state is tracked in llama.cpp with per-token KV cache management.

**Lesson:** For ring topologies, pipeline parallelism is not an optimization — it's a prerequisite for any scaling. Without it, adding devices provides zero throughput benefit.

## 5. 10 Phones is the Sweet Spot

**Impact: Defines optimal cluster size**
**Discovery date:** Feb 18, 2026

Throughput peaks at 10 phones (3.345 tok/s) and declines beyond:

| Region | tok/s trend | Bottleneck |
|--------|------------|------------|
| 4-10 phones | Rising | Per-phone compute dominates |
| 10 phones | **Peak** | Compute and communication balanced |
| 10-20 phones | Declining | Communication overhead dominates |

At 10 phones:
- Per-phone compute: ~72ms (6 layers)
- Communication overhead: ~30ms (10 hops x 3ms)
- Pipeline fill: ~1,000ms (one full ring traversal)

At 20 phones:
- Per-phone compute: ~36ms (3 layers)
- Communication overhead: ~60ms (20 hops x 3ms)
- Pipeline fill: ~1,200ms (more hops)
- Acceptance rate drops to 45.5% (longer verification = stale drafts)

**Lesson:** There's an optimal cluster size for any given model, network latency, and parallelism strategy. Beyond it, coordination overhead outweighs compute benefits. For our setup: 10 phones for 33B.

## 6. Speculative Decoding = 3.2x Over Baseline

**Impact: 3.2x throughput improvement**
**Discovery date:** Feb 17, 2026

The 1.3B draft model runs entirely on rank 0's phone in ~300ms, generating 8 candidate tokens. At 77.8% acceptance (10 phones), each cycle produces ~6 accepted tokens.

| Component | Contribution |
|-----------|-------------|
| Draft overhead | ~305ms per cycle |
| Verification (pipeline) | ~1,840ms per cycle |
| Tokens accepted | ~6 per cycle |
| Effective throughput | 3.345 tok/s (vs 1.04 non-speculative) |

**Key insight:** The draft model must be small enough to fit alongside the target model layers on rank 0, and fast enough that draft generation doesn't dominate the cycle. At 832 MiB, the 1.3B model fits comfortably alongside 5 target layers on a phone with ~5 GiB available RAM.

**Lesson:** Speculative decoding is transformative for ring topologies because it amortizes the high ring latency over multiple tokens. One ring traversal verifies 8 tokens instead of generating 1.

## 7. USB Tunnel Chain Fails at 4+ Phones

**Impact: Blocked scaling for 2 days; led to Ethernet solution**
**Discovery date:** Feb 15-16, 2026

The USB tunnel chain (Phone -> ADB reverse -> WinPC -> SSH tunnel -> Coding Server -> SSH tunnel -> WinPC -> ADB forward -> Phone) works reliably for 3-phone rings but fails at 4+. The ZMQ bcast startup handshake crashes because rank 2's message to rank 3 is lost.

**Root cause:** Never fully determined. Leading theory: ZMQ PUSH `connect()` is asynchronous — if the 4-hop TCP tunnel isn't fully established, ZMQ buffers the message and reports success, but the data never arrives.

**Resolution:** Switched to Ethernet phones with direct IP communication. No tunnels, no relay. This bypassed the bug entirely and reduced per-hop latency from 5-10ms to 1-3ms.

**Lesson:** Complex tunnel chains are brittle. When possible, use direct network communication. The move to Ethernet was the single most important infrastructure decision — it unlocked all scaling work.

## 8. llama.cpp RPC graph_recompute Cache Bug

**Impact: 44% throughput improvement (when using RPC)**
**Discovery date:** Feb 14, 2026

The RPC protocol has a `GRAPH_RECOMPUTE` optimization that should send a 4-byte message instead of re-serializing the full compute graph (~600KB per phone) when the graph topology hasn't changed. The existing cache check used `memcmp` on the full `ggml_tensor` struct, which includes pointer fields (`data`, `buffer`, `src[]`). These pointers change every token even though the graph topology is identical, so the cache never hit during generation.

**Fix:** Compare only topology-relevant fields: `op`, `type`, `flags`, `ne[0..3]`, `nb[0..3]`, `op_params`, and source existence (not pointer values).

**Impact:** Eliminated ~1.8 MB/token of serialization. This is arguably a bug in upstream llama.cpp that should be fixed.

**Lesson:** Always verify that caching mechanisms actually cache. Add metrics/logging to confirm cache hit rates.

## 9. Thread Pinning Matters on big.LITTLE

**Discovery date:** Feb 14, 2026

Snapdragon 888 has asymmetric cores (1x X1 + 3x A78 + 4x A55). Without pinning, the OS scheduler may place inference threads on little cores (A55), which are ~60% slower.

| Config | tok/s | Notes |
|--------|-------|-------|
| No pinning, `-t 8` | ~0.7 | Threads spread across all cores |
| No pinning, `-t 4` | ~0.85 | Fewer threads, but may hit little cores |
| `taskset f0`, `-t 4` | 1.04 | Pinned to big cores 4-7 |

**Lesson:** On big.LITTLE ARM SoCs, always pin compute-intensive threads to big cores. `taskset` is available on Android via `adb shell`.

## 10. Model Loading Strategy

**Discovery date:** Feb 14-15, 2026

Model loading on phones is slow. The 33B Q4_K_M model (18.6 GiB) takes:

| Method | Time | Notes |
|--------|------|-------|
| First load (no cache, USB RPC) | ~20 min | Full tensor transfer over USB |
| Cached load (USB RPC) | ~2 min | Hash cache hits, skip transfers |
| Direct load (Ethernet, --no-mmap) | ~2 min | Read from local storage |

For the ring topology (cellswarm), each phone loads the full model from local storage and only uses its assigned layers. The `--no-mmap` flag forces a full sequential read at startup (~2 min), but this is amortized over the entire inference session.

**Lesson:** Pre-deploy models to phone storage. Never transfer models over the network at inference time.
