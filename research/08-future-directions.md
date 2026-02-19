# 08 — Future Directions

## Near-Term Improvements

### KleidiAI Integration

ARM's KleidiAI kernels claim 25-51% speedup for LLM workloads on ARM CPUs. These are hand-tuned NEON/SVE assembly kernels for quantized matrix multiplication, available in llama.cpp since late 2025.

- **Potential gain:** 25-40% per-phone compute reduction (conservative estimate without i8mm)
- **Projected impact:** At 10 phones, per-rank compute drops from ~72ms to ~45-54ms. Pipeline drain accelerates. Projected throughput: **4.0-4.5 tok/s**.
- **Blocker:** Requires integrating KleidiAI into our prima.cpp fork. The dotprod path should work on Snapdragon 888 even without i8mm.
- **Effort:** Medium — merge upstream llama.cpp KleidiAI support into prima.cpp vendor.

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
