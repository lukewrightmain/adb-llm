# 02 — Methods Survey: ARM CPU Cluster LLM Inference

This document surveys all known methods for running large language models on ARM-based device clusters, as of February 2026.

## Multi-Device Frameworks

### prima.cpp

- **Repository:** https://github.com/nicojbae/prima.cpp
- **Parallelism:** Pipeline ring topology (ZeroMQ sockets)
- **ARM support:** Yes (cross-compilation for ARM64 Android)
- **Cluster support:** Yes (N-device ring over TCP)
- **Key claims:** 15x throughput over llama.cpp for 70B models; 674ms/tok for 70B on mixed hardware; 89ms/tok (11.2 tok/s) for 32B; 26 tok/s with speculative decoding
- **Our use:** Forked and extended with pipeline parallelism (`llama_decode_pipeline()`)

prima.cpp distributes transformer layers across devices in a ring. Each device processes its assigned layers and forwards activations to the next. Unlike tensor parallelism, this requires no synchronization barriers within a layer — each device is independent. The trade-off is that throughput is limited by the sequential pipeline depth.

### llama.cpp RPC

- **Repository:** https://github.com/ggerganov/llama.cpp (built-in RPC backend)
- **Parallelism:** Tensor split (GGML backend splits tensors across RPC servers)
- **ARM support:** Yes (rpc-server compiles for ARM64)
- **Cluster support:** Yes (N RPC servers, one host coordinates)
- **Key claims:** Most mature distributed inference. Benchmarked at 48 tok/s with GPU backends.
- **Our use:** Initial approach. Achieved 1.08 tok/s on 3 phones (see [03-our-approach.md](03-our-approach.md))

RPC distributes individual tensor operations — the host sends `graph_compute` commands and each RPC server processes its shard. This has higher communication overhead per token because every layer requires a round-trip to all servers. We found a critical bug in the `graph_recompute` cache (pointer comparison instead of topology comparison) and fixed it for a 44% speedup.

### distributed-llama

- **Repository:** https://github.com/nicojbae/distributed-llama
- **Parallelism:** Tensor parallel (row/column split)
- **ARM support:** Yes (compiles for ARM64)
- **Cluster support:** Yes (requires power-of-2 node count)
- **Key claims:** Runs on Raspberry Pi clusters. No published ARM-specific throughput numbers.
- **Limitation:** Power-of-2 constraint (must use 2, 4, 8, 16 devices). No published benchmarks on ARM at 33B scale.

### Petals

- **Repository:** https://github.com/bigscience-workshop/petals
- **Parallelism:** P2P pipeline (BitTorrent-style swarm)
- **ARM support:** No (requires CUDA/ROCm)
- **Cluster support:** Yes (Internet-scale P2P)
- **Key claims:** ~6 tok/s for Llama 2 70B on GPU swarm
- **Limitation:** GPU-only. Not applicable to ARM CPU clusters.

### vLLM

- **Repository:** https://github.com/vllm-project/vllm
- **Parallelism:** Tensor parallel + pipeline parallel
- **ARM support:** Experimental ARM backend (CPU)
- **Cluster support:** Yes (Ray-based distribution)
- **Key claims:** Production-grade serving with continuous batching
- **Limitation:** Optimized for GPU clusters. ARM CPU backend is experimental and not benchmarked for multi-device.

### exo

- **Repository:** https://github.com/exo-explore/exo
- **Parallelism:** Auto-partitioned pipeline
- **ARM support:** Limited (Python + llama.cpp engine)
- **Cluster support:** Yes (WiFi/libp2p discovery)
- **Key claims:** "Run your own AI cluster at home"
- **Limitation:** Python overhead, unstable WiFi discovery. OOM on 32B+ models with insufficient device RAM. Our earlier WiFi cluster (9 Pixel phones) used exo but was too unstable for production.

## Summary Table

| Project | Parallelism | ARM? | Cluster? | Key Claim | Our Assessment |
|---------|------------|------|----------|-----------|----------------|
| **prima.cpp** | Pipeline ring | Yes | Yes | 15x over llama.cpp | Best for ARM phone clusters |
| llama.cpp RPC | Tensor split | Yes | Yes | 48 tok/s (GPU) | High per-token comm overhead |
| distributed-llama | Tensor parallel | Yes | Yes | RPi support | Power-of-2 constraint, no benchmarks |
| Petals | P2P pipeline | No ARM | GPU only | 6 tok/s 70B | Not applicable |
| vLLM | Tensor+pipeline | Experimental | Yes | Production serving | GPU-focused |
| exo | Auto-partition | Limited | WiFi/libp2p | Home AI cluster | Unstable, OOM on large models |

## Single-Device Optimizations

These optimize inference on a single ARM device. They complement multi-device frameworks.

### KleidiAI (ARM)

- **Source:** ARM's official inference kernels
- **Claim:** 25-51% speedup on ARM CPUs for LLM workloads
- **Mechanism:** Hand-tuned NEON/SVE/SME kernels for quantized matrix multiplication
- **Status:** Available in llama.cpp since late 2025 (requires ARMv8.6+ for full benefit with i8mm)
- **Our relevance:** Cannot use i8mm path on Snapdragon 888 (ARMv8.4). The dotprod path may still provide gains. Not yet integrated into our prima.cpp fork.

### PowerInfer-2

- **Repository:** https://github.com/SJTU-IPADS/PowerInfer
- **Claim:** 11 tok/s for 7B on a single phone (activation sparsity + GPU offload)
- **Mechanism:** Only computes active neurons per token (~30% of weights), offloads hot neurons to GPU
- **Limitation:** Single-device only, requires GPU, works best with models trained for sparsity. Not applicable to 33B models or CPU-only clusters.

### MLC LLM (Machine Learning Compilation)

- **Repository:** https://github.com/mlc-ai/mlc-llm
- **Claim:** Near-native performance on diverse hardware via TVM compilation
- **Mechanism:** Compiles models through Apache TVM, generates device-specific kernels
- **Limitation:** Single-device, complex compilation pipeline. No multi-device distribution.

### Qualcomm QNN SDK

- **Claim:** Optimized inference on Snapdragon (NPU + GPU + CPU)
- **Mechanism:** Uses Qualcomm's neural processing unit and Adreno GPU via proprietary SDK
- **Limitation:** Only works on Snapdragon 8 Gen 3+ (SM8650+). Our Snapdragon 888 is too old. Single-device only.

### MediaPipe LLM Inference

- **Source:** Google's MediaPipe framework
- **Claim:** On-device LLM inference for Android/iOS
- **Mechanism:** Uses GPU delegates and XNNPack for CPU
- **Limitation:** Single-device, small models (7B max practical). No cluster support.

### AirLLM

- **Repository:** https://github.com/lyogavin/Airllm
- **Claim:** Run 70B models on limited RAM via layer streaming
- **Mechanism:** Loads one layer at a time from storage, processes, unloads
- **Limitation:** Extremely slow (minutes per token). Not viable for interactive use.

## Technique Taxonomy

### Parallelism Strategies

| Strategy | Communication | Scaling | Best For |
|----------|--------------|---------|----------|
| **Tensor parallel** | All-reduce per layer | Bandwidth-limited | Fast interconnect (NVLink, InfiniBand) |
| **Pipeline parallel** | Point-to-point per stage | Latency-limited | Slow interconnect (Ethernet, WiFi) |
| **Ring pipeline** | Sequential forwarding | Pipeline depth | Homogeneous devices, low-bandwidth links |

For ARM phone clusters with 1-3ms Ethernet links, **pipeline/ring parallelism** is the correct choice. Tensor parallelism requires all-reduce synchronization that would be bottlenecked by network latency.

### Speculative Decoding

A small "draft" model generates candidate tokens cheaply. The large "target" model verifies them in a single batched forward pass. If the draft is accurate, multiple tokens are accepted per verification cycle.

Key parameters:
- **Draft model size:** Must fit on one device alongside target layers. Our 1.3B draft fits in ~832 MiB.
- **Draft count:** We use 8 candidates per cycle (--draft-max 8).
- **Acceptance rate:** Higher is better. We see 57-79% depending on phone count (more phones = more layers per rank = longer verification = more stale draft context).

### Quantization Impact

| Quantization | Size (33B) | ARM Fast Path | Notes |
|-------------|-----------|---------------|-------|
| Q4_K_M | 18.6 GiB | dotprod (NEON) | Our primary format. Good quality/size ratio. |
| Q4_0 | ~17 GiB | sgemm (llamafile) | Hits ARM GEMM fast paths. Marginally faster per-token. |
| Q8_0 | ~33 GiB | i8mm (if available) | Too large for our fleet. Would need i8mm for speed benefit. |

### Memory Mapping vs Explicit Loading

| Method | Android Behavior | Performance |
|--------|-----------------|-------------|
| mmap (default) | Kernel page-faults on access, aggressive eviction | 0.011 tok/s (87x slower) |
| **--no-mmap** | **Explicit read into allocated memory** | **1.04 tok/s baseline** |

Android's memory pressure management aggressively evicts mmap'd pages, causing constant page faults during inference. `--no-mmap` forces the entire model into resident memory at load time, eliminating page thrashing. This is the single most impactful optimization in our entire project (see [Lessons Learned](07-lessons-learned.md)).
