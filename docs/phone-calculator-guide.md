# Phone Calculator Guide

How to determine the optimal number of phones for any model on a cellswarm ring cluster with speculative decoding and pipeline parallelism.

This guide walks through the math behind `scripts/phone_calculator.py`. You can either use the script directly or follow the manual steps below.

---

## Quick Start

```bash
# DeepSeek 33B Q4_K_M on Z Fold3 phones
python3 scripts/phone_calculator.py --layers 62 --model-size 18.6 --ram 5.0

# Llama 2 7B Q4_K_M, no speculative decoding
python3 scripts/phone_calculator.py --layers 32 --model-size 4.0 --ram 5.0 --no-spec

# Custom chipset (faster per-layer compute)
python3 scripts/phone_calculator.py --layers 62 --model-size 18.6 --ram 5.0 --per-layer-ms 7
```

---

## Step 1: RAM Budget

First, determine how many phones are needed just to fit the model in RAM.

**Inputs:**
- `model_size_gb`: Total model file size (e.g., 18.6 GB for DeepSeek 33B Q4_K_M)
- `available_ram_gb`: Free RAM per phone after OS overhead (e.g., 5.0 GB on Z Fold3)
- `draft_size_gb`: Draft model size (e.g., 0.85 GB for DeepSeek-Coder 1.3B Q4_K_M)

**Without speculative decoding:**

```
min_phones = ceil(model_size_gb / available_ram_gb)
```

**With speculative decoding:**

Rank 0 must hold both its share of target layers AND the entire draft model:

```
rank0_budget = available_ram_gb - draft_size_gb
min_phones = ceil(model_size_gb / rank0_budget)
```

**Example (DeepSeek 33B):**
```
rank0_budget = 5.0 - 0.85 = 4.15 GB
min_phones = ceil(18.6 / 4.15) = ceil(4.48) = 5 phones
```

So you need at least 5 phones just to fit the model. With 5 phones, each phone holds ~3.72 GB of target layers, and rank 0 holds 3.72 + 0.85 = 4.57 GB total.

---

## Step 2: Layer Distribution

Divide the model's transformer layers across the ring. With speculative decoding, rank 0 gets fewer target layers because it also runs the draft model during each cycle.

**Inputs:**
- `total_layers`: Number of transformer layers (e.g., 62 for DeepSeek 33B)
- `n_phones`: Number of phones in the ring
- `rank0_reduction`: Layers removed from rank 0 (default: 6)

**Formula:**

```
base_layers_per_phone = total_layers / n_phones  (integer division)
rank0_layers = base_layers_per_phone - rank0_reduction
remaining_layers = total_layers - rank0_layers
# Distribute remaining evenly across other ranks
```

**Example (12 phones, DeepSeek 33B):**
```
base = 62 / 12 = 5 layers per phone
rank0 = 5 - 4 = 1 layer  (reduction capped to not go below 1)
remaining = 62 - 1 = 61 layers across 11 phones
= 5 or 6 layers each → [1, 6, 6, 6, 6, 6, 6, 5, 5, 5, 5, 5]
```

---

## Step 3: Per-Cycle Timing Model

This is the core calculation. We model one complete speculative decoding cycle.

### Definitions

| Symbol | Default | Meaning |
|--------|---------|---------|
| `N` | varies | Number of phones |
| `L` | 62 | Total model layers |
| `d` | 24 | Draft tokens per cycle |
| `t_layer` | 12 ms | Compute time per layer (Cortex-A78, Q4_K_M) |
| `t_net` | 18 ms | Network overhead per hop (packed metadata, ethernet) |
| `t_draft` | 38 ms | Draft model time per token |
| `t_resync` | 37 ms | KV cache resync overhead |
| `alpha` | 0.745 | Acceptance rate |

### 3a. Hop Time

The pipeline drain rate is limited by the slowest rank in the ring:

```
max_layers = max(layers_per_rank)
hop_time = max_layers * t_layer + t_net
```

**Example (12 phones):**
```
max_layers = 6  (from distribution [1,6,6,6,6,6,6,5,5,5,5,5])
hop_time = 6 * 12 + 18 = 90 ms
```

### 3b. First Ring Traversal

The first draft token must traverse the entire ring before it can be sampled:

```
first_ring = N * hop_time
```

**Example:** `12 * 90 = 1080 ms`

### 3c. Draft Time

Time to generate all draft tokens autoregressively on rank 0:

```
draft_time = d * t_draft
```

**Example:** `24 * 38 = 912 ms`

### 3d. Phase 1 (Interleaved Draft + Send)

With interleaving, we start sending draft tokens into the ring as soon as each is generated. Draft generation and ring traversal overlap:

```
phase1 = max(draft_time, first_ring)
```

**Example:** `max(912, 1080) = 1080 ms`

The interleave saves ~900ms compared to sequential (draft_time + first_ring).

### 3e. Phase 2 (Pipeline Receive)

After phase 1, remaining verified tokens drain from the pipeline at hop_time intervals:

```
total_pipeline = first_ring + d * hop_time
recv_after_phase1 = total_pipeline - phase1
```

**Example:**
```
total_pipeline = 1080 + 24 * 90 = 3240 ms
recv_after_phase1 = 3240 - 1080 = 2160 ms
```

### 3f. Total Cycle Time

```
cycle_time = phase1 + recv_after_phase1 + t_resync
```

**Example:** `1080 + 2160 + 37 = 3277 ms`

---

## Step 4: Throughput

```
accepted_tokens = (d + 1) * alpha
tok_per_sec = accepted_tokens / (cycle_time / 1000)
```

**Example:**
```
accepted_tokens = (24 + 1) * 0.745 = 18.625
tok_per_sec = 18.625 / 3.277 = 5.68 tok/s
```

Measured: **6.117 tok/s** (the model is ~7% conservative due to simplified pipeline timing).

---

## Step 5: Sweep and Optimize

Run the calculation for each phone count from `min_phones` to `max_phones` and pick the configuration with the highest tok/s:

```bash
python3 scripts/phone_calculator.py --layers 62 --model-size 18.6 --ram 5.0
```

The script sweeps all valid phone counts and reports the optimal.

**Important caveat:** The theoretical model is optimistic at high phone counts (>15). Real-world coordination overhead, ZMQ jitter, and ring synchronization costs grow with the number of hops. Trust the model for the general shape of the curve, but validate with benchmarks around the predicted optimum.

---

## Worked Example 1: DeepSeek 33B on Z Fold3

| Parameter | Value |
|-----------|-------|
| Model | DeepSeek-Coder 33B Q4_K_M |
| Layers | 62 |
| Model size | 18.6 GB |
| Draft model | DeepSeek-Coder 1.3B Q4_K_M (0.85 GB) |
| Phone | Samsung Galaxy Z Fold3 |
| Available RAM | 5.0 GB |
| SoC | Snapdragon 888 (Cortex-A78) |
| Per-layer compute | 12 ms |

**Step 1 — RAM:**
```
min_phones = ceil(18.6 / (5.0 - 0.85)) = ceil(4.48) = 5
```

**Step 2 — Layer distribution (12 phones):**
```
[1, 6, 6, 6, 6, 6, 6, 5, 5, 5, 5, 5]
```

**Step 3 — Timing:**
```
hop_time      = 6 * 12 + 18 = 90 ms
first_ring    = 12 * 90 = 1080 ms
draft_time    = 24 * 38 = 912 ms
phase1        = max(912, 1080) = 1080 ms
recv_phase2   = (1080 + 24 * 90) - 1080 = 2160 ms
cycle_total   = 1080 + 2160 + 37 = 3277 ms
```

**Step 4 — Throughput:**
```
accepted = 25 * 0.745 = 18.625 tokens
tok/s = 18.625 / 3.277 = 5.68
```

**Measured result:** 6.117 tok/s at 12 phones (model underestimates by ~7%).

---

## Worked Example 2: Llama 2 7B on Z Fold3

| Parameter | Value |
|-----------|-------|
| Model | Llama 2 7B Q4_K_M |
| Layers | 32 |
| Model size | 4.0 GB |
| Draft model | TinyLlama 1.1B Q4_K_M (0.65 GB) |
| Available RAM | 5.0 GB |

```bash
python3 scripts/phone_calculator.py --layers 32 --model-size 4.0 --ram 5.0 --draft-size 0.65
```

**Step 1 — RAM:**
```
min_phones = ceil(4.0 / (5.0 - 0.65)) = ceil(0.92) = 1
```

A single phone can fit both models! But is speculative worth it on 1 phone?

**1 phone (sequential, no speculative):**
```
ring_latency = 32 * 12 + 18 = 402 ms
tok/s = 1000 / 402 = 2.49 tok/s
```

**2 phones (speculative + pipeline):**
```
layers = [10, 22]  → max = 22
hop_time = 22 * 12 + 18 = 282 ms
first_ring = 2 * 282 = 564 ms
phase1 = max(912, 564) = 912 ms
recv = 564 + 24 * 282 - 912 = 6420 ms
cycle = 912 + 6420 + 37 = 7369 ms
tok/s = 18.625 / 7.369 = 2.53 tok/s
```

**3 phones (speculative + pipeline):**
```
layers = [4, 14, 14] → max = 14
hop_time = 14 * 12 + 18 = 186 ms
first_ring = 3 * 186 = 558 ms
phase1 = max(912, 558) = 912 ms
recv = 558 + 24 * 186 - 912 = 4110 ms
cycle = 912 + 4110 + 37 = 5059 ms
tok/s = 18.625 / 5.059 = 3.68 tok/s
```

For 7B models, 3 phones gives a reasonable 3.7 tok/s with speculative decoding, or 2.5 tok/s on 1 phone without it. The sweet spot depends on available hardware — if you only have 2-3 phones, the 7B model will run fine.

---

## Worked Example 3: Llama 2 70B

| Parameter | Value |
|-----------|-------|
| Model | Llama 2 70B Q4_K_M |
| Layers | 80 |
| Model size | 40.0 GB |
| Draft model | 1.3B Q4_K_M (0.85 GB) |
| Available RAM | 5.0 GB |

```
min_phones = ceil(40.0 / (5.0 - 0.85)) = ceil(9.64) = 10
```

At 10 phones with speculative:
```
layers = [2, 9, 9, 9, 8, 8, 8, 8, 8, 8, ...] → max = 9
hop_time = 9 * 12 + 18 = 126 ms
first_ring = 10 * 126 = 1260 ms
phase1 = max(912, 1260) = 1260 ms
recv = 1260 + 24 * 126 - 1260 = 3024 ms
cycle = 1260 + 3024 + 37 = 4321 ms
tok/s = 18.625 / 4.321 = 4.31 tok/s
```

70B models need at least 10 phones and benefit from 12-15 for better layer balance.

---

## Quick Reference: Common Models

Estimated tok/s on Z Fold3 (Snapdragon 888, 5.0 GB RAM, ethernet, packed metadata):

| Model | Layers | Size (Q4_K_M) | Min Phones | Est. Optimal | Est. tok/s |
|-------|--------|---------------|------------|--------------|------------|
| Llama 2 7B | 32 | 4.0 GB | 1 | 1 (no spec) | ~2.5 |
| Llama 2 13B | 40 | 7.4 GB | 2 | 5-6 | ~4.5 |
| DeepSeek 33B | 62 | 18.6 GB | 5 | 10-12 | ~5.7 |
| Llama 2 70B | 80 | 40.0 GB | 10 | 14-16 | ~5.0 |
| Mixtral 8x7B | 32 | 24.6 GB | 6 | 8-10 | ~5.5 |

Notes:
- "Est. Optimal" is the phone count that maximizes tok/s
- Real performance may vary 5-15% from estimates
- Acceptance rate strongly affects speculative performance (varies by prompt/seed)
- For models that fit on 1-2 phones, non-speculative may be simpler and nearly as fast
- See `docs/chipset-reference.md` for per-layer estimates on other Snapdragon SoCs

---

## Tuning Parameters

### Acceptance Rate

The acceptance rate (`alpha`) has the biggest impact on speculative throughput. It varies by:
- **Prompt type**: Code completion tends to have higher acceptance (~75-85%)
- **Generation length**: Acceptance degrades after ~128 tokens (~60% at 256 tokens)
- **Seed/sampling**: Different seeds give wildly different rates (11-85% observed)
- **Draft model quality**: Larger draft models have higher acceptance but longer draft time

If you're seeing lower throughput than expected, check the acceptance rate first.

### Draft Tokens (d-max)

More draft tokens per cycle = more potential tokens accepted, but:
- Longer cycles if acceptance is low (wasted verification work)
- Pipeline drain time grows linearly with d
- Sweet spot is typically d=16-24 for acceptance rates of 60-80%

### Network Overhead

Depends on your network setup:
- **Ethernet (packed metadata)**: ~18 ms per hop (our production setup)
- **WiFi direct**: ~25-40 ms per hop (varies with interference)
- **USB ADB tunnels**: ~20-30 ms per hop (stable but limited by USB)

### Per-Layer Compute

Depends on the chipset and quantization. See `docs/chipset-reference.md` for estimates by SoC:
- Cortex-A78 (SD 888): 12 ms (measured)
- Cortex-A710 + i8mm (SD 8 Gen 1): ~8-9 ms (estimated)
- Cortex-X4 (SD 8 Gen 3): ~5-6 ms (estimated)

Use `--per-layer-ms` to adjust for your hardware.
