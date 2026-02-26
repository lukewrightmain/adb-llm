# 08 — Future Directions

## Near-Term Improvements

### KleidiAI Integration (Tested — NO BENEFIT for Q4_K_M, feature/kleidiai-optimization branch)

ARM's KleidiAI kernels claim 25-51% speedup for LLM workloads on ARM CPUs. These are hand-tuned NEON/SVE assembly kernels for quantized matrix multiplication, available in llama.cpp since late 2025.

**Verdict: KleidiAI only supports Q4_0 and Q8_0 quantization — NOT Q4_K_M. No benefit for our pipeline.**

**Results (2026-02-25, DeepSeek-Coder 1.3B Q4_K_M on SD888):**

| Configuration | Prefill (pp512) | Decode (tg128) |
|---|---|---|
| Upstream (no KleidiAI) | 61.29 tok/s | 27.30 tok/s |
| Upstream + Flash Attn | 60.31 tok/s | **28.70 tok/s** |
| Upstream + KleidiAI | 62.52 tok/s | 26.22 tok/s |
| Upstream + KleidiAI + FA | 61.38 tok/s | 28.05 tok/s |

- KleidiAI has zero effect on Q4_K_M (only fires for Q4_0/Q8_0)
- Flash attention gives ~5% decode improvement — free win, no code changes
- Q4_0 was already tested and was worse: 3.3 vs 5.8 tok/s ring throughput (40.7% vs 72% draft acceptance)
- Upstream has repack multi-row GEMV (8-row parallel `vdotq_laneq_s32`) but only helps at larger matrix dims
- Upstream vs vendored essentially identical for 1.3B (27.30 vs ~26 tok/s)

**Actionable takeaway:** Enable flash attention in cellswarm build for free ~5% decode boost. KleidiAI not useful until Q4_K_M support is added upstream (or we switch to Q4_0, which has unacceptable quality loss).

### Hexagon 780 NPU Testing (Completed — NO-GO, feature/npu-testing branch)

The Snapdragon 888 (SM8350) has a **Hexagon 780 NPU (26 TOPS INT8)** that was tested via Qualcomm QNN SDK 2.35.0. This is HTP v68 architecture: INT8 only, no FP16/INT4 support, 32-bit address space.

**Verdict: NPU is slower than CPU at all LLM-relevant sizes. Phases 4-5 cancelled.**

**Results (2026-02-25):**

| Config | M | NPU (ms) | CPU NEON (ms) | Speedup |
|--------|---|----------|---------------|---------|
| 1.3B decode (2048²) | 1 | 55.8 | 10.7 | 0.19x |
| 1.3B prefill (2048²) | 128 | 59.9 | 33.2 | 0.55x |
| 7B decode (4096²) | 1 | 144.9 | 117.5 | 0.81x |
| 7B prefill (4096²) | 128 | 202.0 | 226.6 | **1.12x** |
| FFN decode (11008×4096) | 1 | 386.9 | 750.5 | **1.94x** |
| Layer total (7 ops, 1.3B) | 1 | 530.1 | 315.3 | 0.59x |
| Layer total (7 ops, 1.3B) | 128 | 617.6 | 611.0 | 0.99x |

**Root cause:** ~50ms minimum NPU dispatch overhead per graphExecute() call (DSP context switch + DMA setup). For small matmuls that take 10-15ms on CPU, this overhead is 3-5x the actual compute. NPU only wins at very large matrices (11008×4096 FFN) where CPU takes >500ms. Effective TOPS: 0.0002-0.023 (< 0.1% of 26 TOPS theoretical).

**Speculative decoding impact:** NPU draft model (1.3B, d24) takes 303s vs CPU 162s (1.87x slower). Zero throughput improvement.

**What worked:** CDSP access from `/data/local/tmp/` succeeded without root. QNN graph creation, tensor registration, finalization, and execution all functional. SELinux was not a blocker.

**What failed:** Single-op matmul graphs can't amortize DSP dispatch overhead. Would need fused full-layer graphs to be competitive, which is a much larger engineering effort for uncertain gains.

Full data: `npu/results/20260225_*.tsv`

### Qwen2.5-Coder-32B Benchmarks (Tested — 2.85 tok/s on 7 phones)

Tested Qwen2.5-Coder-32B-Instruct Q4_K_M (18.5 GiB, 64 layers) as an alternative to DeepSeek-Coder-33B on the cellswarm phone ring. Uses same-family Qwen2.5-Coder-1.5B-Instruct Q4_K_M (941 MiB) as draft model.

**Key finding: DeepSeek-33B outperforms Qwen2.5-32B on our pipeline by ~27%, driven by higher draft acceptance rate.**

**Qwen3.5 was NOT tested** — Gated DeltaNet architecture (released Feb 24, 2026) is unsupported in our vendored llama.cpp fork. No compatible draft model exists (Qwen3 0.6B has 151K vocab vs Qwen3.5's 248K vocab). Would require major porting effort.

**Results (2026-02-25, 7 phones, cellswarm-master, d24, seed 42):**

| Model | Layers | Server tok/s | Acceptance | Tokens/cycle | Cycle time |
|-------|--------|-------------|-----------|--------------|------------|
| DeepSeek-Coder-33B + 1.3B draft | 62 | **3.63** | **69.5%** | **14.6** | ~5000ms |
| Qwen2.5-Coder-32B + 1.5B draft | 64 | 2.85 | 54.8% | 10.1 | ~4500ms |

**Per-cycle breakdown (d24, steady-state):**

| Metric | DeepSeek-33B (7p) | Qwen2.5-32B (7p) |
|--------|-------------------|-------------------|
| draft+phase1 | ~1040ms | ~1180ms |
| recv (ring verification) | ~3900ms | ~3300ms |
| total/cycle | ~5000ms | ~4500ms |

**Observations:**
- Qwen2.5 ring passes are ~600ms faster (smaller FFN intermediate: 27648 vs 33792)
- But 55% acceptance vs 70% means fewer tokens per cycle, negating the speed advantage
- Same-family drafting (Qwen2.5-1.5B→32B: 55%) is better than cross-family (DeepSeek-1.3B→Qwen2.5-32B: 43%) but worse than DeepSeek-1.3B→33B (70%)
- Peak Qwen2.5 cycles hit 24/24 acceptance (5.3 tok/s) — competitive in best-case
- Adaptive draft controller oscillates; fixed d12 or d24 might perform better
- 3-phone ring is impractical (20s/token) — minimum 7 phones needed for >2 tok/s

**Scaling estimate (based on DeepSeek 12p=5.50 tok/s scaling curve):**
- 7 phones: 2.85 tok/s (measured)
- 12 phones: ~4.4 tok/s (estimated, if 12 phones had models)
- Currently only 7 phones online (14 down — ethernet connectivity issues)

### Newer Hardware (Snapdragon 8 Gen 3+)

The Snapdragon 8 Gen 3 (SM8650, released late 2023) brings:
- **ARMv9.2-A** with i8mm and SVE2 extensions
- **Adreno 750** with functional Vulkan/OpenCL compute
- **Qualcomm QNN SDK** for NPU acceleration
- **LPDDR5X** with higher bandwidth

Expected benefits:
- i8mm enables Q4_0 ARM GEMM fast paths (~30-50% compute improvement)
- GPU offload (ngl=10-20 layers) could halve per-phone compute time
- NPU acceleration for specific operations (dequantization, attention)

**Projected impact:** 2-3x per-phone compute improvement. At 10 phones: **6-10 tok/s**.

**Challenge:** Used Snapdragon 8 Gen 3 phones (Samsung S24 Ultra, etc.) are currently $500-800, vs $150-200 for Z Fold3. Cost per tok/s may not improve until prices drop.

### 70B Model Across 20+ Phones

Our current fleet of 40 phones (20 USB + 20 Ethernet) could theoretically run a 70B model (~37 GiB Q4_K_M):
- Minimum ~8 phones to fit in memory (~5 GiB/phone)
- Optimal configuration: likely 15-20 phones
- Expected throughput: 2-3 tok/s (based on scaling curve shape)

**Blockers:**
- Need to merge USB and Ethernet phone fleets (or use all 20 Ethernet phones)
- 70B model must be pushed to all phones (~37 GiB each, ~2 hours over ADB)
- Layer distribution needs retuning for 70B's different architecture (80 layers)

### Improved Draft Models

The current draft (DeepSeek Coder 1.3B) achieves 57-79% acceptance. Better draft models could improve this:

| Draft Option | Size | Expected Acceptance | Draft Speed |
|-------------|------|-------------------|-------------|
| Current (1.3B Q4_K_M) | 832 MiB | 57-79% | ~305ms/8tok |
| 3B distilled from 33B | ~1.8 GiB | 80-90% (estimated) | ~600ms/8tok |
| 0.5B aggressive quant | ~300 MiB | 40-50% | ~150ms/8tok |

A faster, less accurate draft model might work better because it reduces the fixed cycle overhead. The 300ms draft loop is ~14% of the total cycle — cutting it in half to 150ms would improve throughput by ~7%.

## Medium-Term Research

### Dynamic Device Joining

Currently, all phones must be present at startup. A production system should support:
- New phones joining a running cluster
- Phones leaving gracefully (layer redistribution)
- Automatic failover when a phone disconnects

**Design:** Initial design notes exist but are not yet formalized into a specification.

### Dual-Ring Topology

Run two independent rings in parallel, each handling different requests:
- Ring A: 10 phones for user 1
- Ring B: 10 phones for user 2
- Controller: Routes requests, manages ring membership

This provides ~6.7 tok/s aggregate throughput for 2 concurrent users with 20 phones.

### Continuous Batching

For multi-user serving, continuous batching processes multiple requests simultaneously:
- Different users' tokens interleave in the pipeline
- Higher GPU/CPU utilization
- Standard in production serving (vLLM, TensorRT-LLM)

Adapting continuous batching to a ring topology is non-trivial — the ring processes one token at a time through all layers, so batching multiple users' tokens requires careful KV cache management across distributed ranks.

### Prefill Optimization

Our current benchmarks measure generation (decode) throughput. Prefill (processing the prompt) is also important for interactive use:
- Prefill currently processes all prompt tokens sequentially through the ring
- Pipeline parallelism helps here too (multiple prompt tokens in flight)
- For 512-token prompts, prefill takes ~30-60 seconds

Optimizing prefill would improve time-to-first-token for interactive applications.

## Long-Term Directions

### ARM Server Chips (Graviton, Ampere)

ARM server CPUs (AWS Graviton 4, Ampere Altra) have:
- 64-128 cores (all big cores, no little)
- ARMv9 with SVE2 and i8mm
- 256-512 GB RAM
- 100 Gbps networking

A cluster of 2-4 ARM servers could run 70B-405B models at 10-50 tok/s. Our ring topology and pipeline parallelism code would transfer directly. The main difference is per-node compute would be 10-50x faster, shifting the bottleneck entirely to the ring communication.

### Heterogeneous Clusters

Mix phone and server nodes:
- Server node: Handles 40+ layers (most compute)
- Phone nodes: Handle remaining layers (edge compute)
- Useful for edge deployments where a server handles the bulk of work and phones provide the last mile

### Model Architecture Innovations

- **Mixture of Experts (MoE):** Only activate a subset of experts per token. Reduces per-token compute but requires routing logic. Good fit for ring topology if experts are co-located.
- **Layer skipping:** Some layers contribute little to output quality. Dynamically skip them to reduce ring depth.
- **Early exit:** Stop inference at an intermediate layer if confidence is high enough. Reduces effective ring traversal per token.

### Compression and Caching

- **KV cache compression:** Reduce memory footprint of the KV cache, allowing longer contexts on phones with limited RAM
- **Activation caching:** Cache intermediate activations for frequently-seen prefixes
- **Speculative prefetching:** Start processing the next likely request while finishing the current one
