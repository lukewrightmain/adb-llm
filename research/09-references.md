# 09 — References

## Our Project

- **adb-llm repository:** Local at `/home/l4090s/adb-llm/`
- **Results:** [RESULTS.md](../RESULTS.md)
- **RPC optimization details:** [docs/optimize-rpc-inference.md](../docs/optimize-rpc-inference.md)
- **Project README:** [README.md](../README.md)

## Frameworks and Libraries

### prima.cpp
- **Repository:** https://github.com/nicojbae/prima.cpp
- **Description:** Pipeline ring parallelism for llama.cpp. Distributes transformer layers across devices in a ZeroMQ ring topology.
- **License:** MIT

### llama.cpp
- **Repository:** https://github.com/ggerganov/llama.cpp
- **Description:** C/C++ LLM inference. Includes RPC backend for distributed inference, extensive quantization support, and ARM optimizations.
- **License:** MIT
- **Relevant components:** RPC backend (`ggml/src/ggml-rpc/`), GGML tensor library, quantization formats

### distributed-llama
- **Repository:** https://github.com/nicojbae/distributed-llama
- **Description:** Tensor-parallel distributed inference. Supports Raspberry Pi clusters with power-of-2 node count requirement.
- **License:** MIT

### Petals
- **Repository:** https://github.com/bigscience-workshop/petals
- **Description:** P2P pipeline parallelism for LLM inference over the Internet. GPU-only.
- **License:** MIT

### vLLM
- **Repository:** https://github.com/vllm-project/vllm
- **Description:** High-throughput LLM serving with PagedAttention, continuous batching, tensor/pipeline parallelism.
- **License:** Apache 2.0

### exo
- **Repository:** https://github.com/exo-explore/exo
- **Description:** Run your own AI cluster at home. Auto-partitioning with WiFi/libp2p discovery.
- **License:** GPL-3.0

### ZeroMQ (libzmq)
- **Repository:** https://github.com/zeromq/libzmq
- **Description:** High-performance messaging library used for inter-phone communication in our ring topology.
- **License:** MPL-2.0

## Single-Device Inference

### PowerInfer-2
- **Repository:** https://github.com/SJTU-IPADS/PowerInfer
- **Paper:** "PowerInfer-2: Fast Large Language Model Inference on a Smartphone" (arXiv:2406.06282)
- **Description:** Activation sparsity + GPU offload for on-device LLM inference.

### MLC LLM
- **Repository:** https://github.com/mlc-ai/mlc-llm
- **Description:** Machine Learning Compilation for LLMs. Uses Apache TVM to generate device-specific inference kernels.
- **License:** Apache 2.0

### KleidiAI (ARM)
- **Source:** https://gitlab.arm.com/kleidi/kleidiai
- **Blog:** https://community.arm.com/arm-community-blogs/b/ai-and-ml-blog/posts/accelerating-llm-inference-on-arm
- **Description:** ARM's official optimized inference kernels. Claims 25-51% speedup on ARM CPUs for LLM workloads.

### Qualcomm QNN SDK
- **Source:** https://developer.qualcomm.com/software/qualcomm-ai-engine-direct
- **Description:** Neural network inference on Qualcomm Snapdragon (NPU + GPU + CPU). Requires Snapdragon 8 Gen 3+.

### AirLLM
- **Repository:** https://github.com/lyogavin/Airllm
- **Description:** Run 70B models on limited RAM via layer-by-layer streaming from storage. Very slow but functional.

## Models

### DeepSeek Coder 33B Instruct (Target)
- **HuggingFace:** https://huggingface.co/deepseek-ai/deepseek-coder-33b-instruct
- **GGUF (Q4_K_M):** https://huggingface.co/TheBloke/deepseek-coder-33B-instruct-GGUF
- **Size:** 18.6 GiB (Q4_K_M), 62 transformer layers
- **Parameters:** 33 billion

### DeepSeek Coder 1.3B Instruct (Draft)
- **HuggingFace:** https://huggingface.co/deepseek-ai/deepseek-coder-1.3b-instruct
- **GGUF (Q4_K_M):** https://huggingface.co/TheBloke/deepseek-coder-1.3b-instruct-GGUF
- **Size:** ~832 MiB (Q4_K_M), 24 transformer layers
- **Parameters:** 1.3 billion

## Hardware Documentation

### Qualcomm Snapdragon 888 (SM8350)
- **Specs:** https://www.qualcomm.com/products/mobile/snapdragon/smartphones/snapdragon-8-series-mobile-platforms/snapdragon-888-5g-mobile-platform
- **CPU:** Kryo 680 (1x Cortex-X1 @ 2.84 GHz + 3x A78 @ 2.42 GHz + 4x A55 @ 1.80 GHz)
- **GPU:** Adreno 660
- **ISA:** ARMv8.4-A (dotprod, fp16; NO i8mm)

### Samsung Galaxy Z Fold3 (SM-F926U/U1/W)
- **Specs:** https://www.samsung.com/us/smartphones/galaxy-z-fold3-5g/
- **RAM:** 12 GB LPDDR5 (~5-5.7 GB available)
- **Storage:** 256 GB

### ARM Architecture Reference
- **ARM ISA Extensions:** https://developer.arm.com/documentation/102378/latest/
- **NEON Intrinsics:** https://developer.arm.com/architectures/instruction-sets/intrinsics/
- **big.LITTLE:** https://developer.arm.com/documentation/102091/latest/

## Papers and Blog Posts

### Speculative Decoding
- Leviathan et al., "Fast Inference from Transformers via Speculative Decoding" (arXiv:2211.17192)
- Chen et al., "Accelerating Large Language Model Decoding with Speculative Sampling" (arXiv:2302.01318)

### Pipeline Parallelism
- Huang et al., "GPipe: Efficient Training of Giant Neural Networks using Pipeline Parallelism" (arXiv:1811.06965)
- Narayanan et al., "PipeDream: Pipeline Parallelism for DNN Training" (SOSP 2019)

### Quantization
- Dettmers et al., "GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers" (arXiv:2210.17323)
- Frantar et al., "Optimal Brain Compression" (arXiv:2208.11580)

### Edge/Mobile LLM Inference
- Xu et al., "PowerInfer-2: Fast Large Language Model Inference on a Smartphone" (arXiv:2406.06282)
- "LLM Inference on ARM CPUs" — ARM Community Blog, 2025
- Raspberry Pi cluster inference blog posts (various, 2024-2025)

## Tools

### ADB (Android Debug Bridge)
- **Documentation:** https://developer.android.com/tools/adb
- **Our binary:** `~/.local/bin/adb` (Linux x86_64, keys copied from WinPC)

### Android NDK r26d
- **Download:** https://developer.android.com/ndk/downloads
- **Our install:** `~/android-ndk/android-ndk-r26d/`
- **Cross-compilation target:** `aarch64-linux-android28`

### CMake / Ninja
- **Our installs:** `~/.local/bin/cmake`, `~/.local/bin/ninja`
- **Minimum version:** CMake 3.14+
