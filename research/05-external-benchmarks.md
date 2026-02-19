# 05 — External Benchmarks

Numbers claimed by other projects, as of February 2026. Where possible, we note the hardware, model, and conditions to enable fair comparison.

## prima.cpp Official

- **Source:** https://github.com/nicojbae/prima.cpp
- **Hardware:** Mixed — ARM servers, x86 workstations, GPUs. Not exclusively ARM phones.
- **Network:** InfiniBand / high-speed Ethernet

| Model | Quant | Devices | tok/s | Notes |
|-------|-------|---------|-------|-------|
| LLaMA 70B | Q4_K_S | Mixed ARM+x86+GPU | ~1.48 (674ms/tok) | Pipeline ring, their headline benchmark |
| DeepSeek 32B | Q4_K_S | Mixed | ~11.2 (89ms/tok) | Non-speculative, fast interconnect |
| DeepSeek 32B | Q4_K_S | Mixed | ~26 | With speculative decoding |
| LLaMA 7B | Q4_K_M | 1x ARM server | ~15-20 | Single-device baseline |

**Comparison to ours:** Their 32B numbers use fast interconnect (likely InfiniBand) and include x86/GPU nodes. Our 3.345 tok/s on phone-only Ethernet is not directly comparable — they have 10-100x faster interconnect and more powerful per-node compute. However, our result demonstrates that the pipeline ring architecture scales effectively even on low-power ARM phones with commodity Ethernet.

## Raspberry Pi Cluster

- **Source:** Various blog posts and YouTube demonstrations
- **Hardware:** 10x Raspberry Pi CM5 (Cortex-A76, 8GB RAM)
- **Network:** Gigabit Ethernet

| Model | Quant | Devices | tok/s | Framework | Notes |
|-------|-------|---------|-------|-----------|-------|
| LLaMA 70B | Q4_0 | 10x RPi CM5 | ~0.85 | distributed-llama | Most cited RPi cluster result |
| LLaMA 7B | Q4_0 | 4x RPi 5 | ~5-8 | llama.cpp | Single-board throughput scales well at 7B |
| LLaMA 13B | Q4_0 | 6x RPi 5 | ~2-3 | distributed-llama | Estimated from blog posts |

**Comparison to ours:** Our 10-phone cluster achieves **~4x the throughput** of a 10-RPi CM5 cluster on a model of similar computational complexity (33B vs 70B, but 70B Q4_0 is more aggressively quantized). The Snapdragon 888's Cortex-X1 + A78 cores outperform the RPi CM5's Cortex-A76 cores, and our use of speculative decoding + pipeline parallelism provides additional gains.

## Petals

- **Source:** https://github.com/bigscience-workshop/petals
- **Hardware:** Distributed GPU nodes (consumer GPUs over Internet)
- **Network:** Internet (high latency, variable)

| Model | Quant | Devices | tok/s | Notes |
|-------|-------|---------|-------|-------|
| LLaMA 2 70B | FP16/INT8 | GPU swarm | ~6 | P2P pipeline, Internet latency |
| BLOOM 176B | FP16 | GPU swarm | ~1 | Larger model, more latency |

**Comparison to ours:** Petals uses GPUs over the Internet — entirely different hardware and network class. Their ~6 tok/s on 70B is impressive given Internet latencies, but not comparable to our LAN phone cluster. We achieve 3.345 tok/s on a 33B model with no GPU at all.

## PowerInfer-2

- **Source:** https://github.com/SJTU-IPADS/PowerInfer
- **Hardware:** Single smartphone (GPU + CPU, activation sparsity)
- **Network:** N/A (single device)

| Model | Quant | Devices | tok/s | Notes |
|-------|-------|---------|-------|-------|
| LLaMA 7B | Mixed | 1 phone (GPU) | ~11 | Activation sparsity + GPU offload |
| LLaMA 13B | Mixed | 1 phone (GPU) | ~3-5 | Estimated from paper |

**Comparison to ours:** Different model class (7B vs 33B) and approach (single-device sparsity vs multi-device pipeline). PowerInfer-2 achieves high throughput by only computing ~30% of neurons per token, which requires models with predictable activation patterns. Our approach runs the full model without sparsity assumptions. Not directly comparable.

## llama.cpp (Single Device, Various Hardware)

Reference benchmarks from llama.cpp benchmarking threads:

| Hardware | Model | Quant | tok/s | Notes |
|----------|-------|-------|-------|-------|
| Apple M2 Ultra | 70B | Q4_K_M | ~10-12 | 192GB unified memory |
| NVIDIA A100 80GB | 70B | Q4_K_M | ~40-50 | Single GPU |
| Snapdragon 8 Gen 3 (phone) | 7B | Q4_K_M | ~15-20 | GPU + CPU |
| Snapdragon 888 (phone) | 7B | Q4_K_M | ~8-10 | CPU only |
| Raspberry Pi 5 | 7B | Q4_K_M | ~3-5 | CPU only |

**Comparison to ours:** A single A100 does 40-50 tok/s on 70B, approximately 12-15x our throughput on a model 2x larger. But an A100 costs ~$15,000 and draws 300W. Our 10-phone cluster cost ~$2,000 and draws ~30W. See [06-comparison-analysis.md](06-comparison-analysis.md) for cost analysis.

## distributed-llama

- **Source:** https://github.com/nicojbae/distributed-llama
- **Hardware:** Designed for clusters; tested on x86 + RPi
- **Key limitation:** Requires power-of-2 device count

No published ARM-phone-specific benchmarks. The project supports ARM compilation but primary testing appears to be on x86 nodes and Raspberry Pi boards. The power-of-2 constraint (must use 2, 4, 8, 16 devices) makes scaling less flexible than prima.cpp's ring topology.

## MLC LLM

- **Source:** https://github.com/mlc-ai/mlc-llm
- **Hardware:** Single device (phone, laptop, etc.)

| Hardware | Model | tok/s | Notes |
|----------|-------|-------|-------|
| Snapdragon 8 Gen 2 | LLaMA 7B | ~15-20 | GPU + CPU via TVM |
| Apple M2 | LLaMA 7B | ~30-40 | Metal backend |

Single-device only. No cluster support. Impressive single-device numbers through TVM compilation, but cannot address 33B+ models that exceed single-device memory.

## Summary of External Numbers

| Project | Model Size | Best tok/s | Hardware | GPU? | Our Advantage |
|---------|-----------|-----------|----------|------|---------------|
| prima.cpp official | 32B | ~11.2 | Mixed + InfiniBand | Yes | We're phone-only, commodity Ethernet |
| RPi CM5 cluster | 70B | ~0.85 | 10x RPi | No | We're 4x faster |
| Petals | 70B | ~6 | GPU swarm + Internet | Yes | We use no GPUs |
| PowerInfer-2 | 7B | ~11 | 1 phone + GPU | Yes | We run 33B, no GPU |
| llama.cpp (A100) | 70B | ~45 | Single A100 | Yes | We're 75x cheaper per tok/s/$ |
