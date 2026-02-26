# Qwen3.5 Port to Cellswarm — Current Status & Problems

## What We're Doing

Porting Qwen3.5 (Gated DeltaNet + Attention hybrid architecture) support to the vendored cellswarm llama.cpp fork for benchmarking on the phone ring cluster.

## Architecture Challenge

Qwen3.5 is a **hybrid recurrent/attention model**:
- **75% recurrent layers** (Gated DeltaNet linear attention with conv1d + SSM state)
- **25% standard attention layers** (GQA with RoPE, every 4th layer)
- Pattern: 3 recurrent → 1 attention, repeating across 64 layers

This requires a **dual memory system**:
- Attention layers need a **position-indexed KV cache** (kv_size = n_ctx, e.g. 256 cells)
- Recurrent layers need a **sequence-indexed state store** (rs_size = n_seq_max, e.g. 3 cells)
- These have fundamentally different sizes and indexing, so they can't share one cell array

## Models Downloaded

| Model | Size | Params | Status |
|-------|------|--------|--------|
| Qwen3.5-27B Q4_K_M | 15.58 GiB | 26.90B | Loads, warmup passes, generates tokens |
| Qwen3.5-35B-A3B Q4_K_M | ~20 GiB | 35B (MoE, 3B active) | Downloaded, not yet tested |

## What Works

1. **Enum/tensor/hparam loading** — `LLM_ARCH_QWEN35` and `LLM_ARCH_QWEN35MOE` added, tensors mapped, model loads correctly
2. **Graph builder** — `build_qwen35()` and `build_qwen35moe()` implemented with Gated DeltaNet (conv1d + delta-net autoregressive) for recurrent layers and gated GQA for attention layers
3. **Hybrid KV cache initialization** — Three-way init: hybrid-recurrent (F32, rs_size cells), hybrid-attention (type_k/v, kv_size cells), non-hybrid (original behavior). Buffer allocates correctly at ~450 MiB
4. **Hybrid state management** — `rs_cells[]`, `rs_size`, `rs_head`, `rs_n` added to `llama_kv_cache` struct; all cache management functions (clear, seq_rm, seq_cp, seq_keep, seq_add) updated
5. **Build succeeds** — Full cellswarm build passes with all changes (dense + MOE)
6. **Warmup passes** — Server starts, model loads, worst-case graph builds, warmup decode succeeds
7. **Token generation works** — 1.61 tok/s decode on x86_64 host (CPU-only, 8 threads)

## Current Problems

### 1. Degenerate Output (Quality)

The model generates tokens but output is repetitive/degenerate:
- Raw completion: "the capital of the capital of..." (stuck in loop)
- Chat completion: `<think><think><think>...` (repeating think tags)
- This suggests the **delta-net recurrent state update** has a bug, or the state isn't being carried forward correctly between tokens
- Alternatively, could be a numerical issue in the softplus/gate computation

**Investigation needed:** Compare intermediate tensor values against a reference implementation (e.g., upstream llama.cpp with Qwen3.5 support).

### 2. Context Shift Crash

When the KV cache fills (n_ctx tokens generated), the server does a "context shift" which re-processes all remaining tokens at once. This hits `GGML_ASSERT(n_seq_tokens == 1)` in the delta-net path.

**Root cause:** Delta-net autoregressive requires single-token decoding. Context shift sends a full-context batch.

**Fix needed:** Either disable context shift for hybrid models (use a large enough n_ctx), or implement multi-token delta-net (complex). For phone deployment, n_ctx=4096+ should avoid this in practice.

### 3. Save/Load State Inconsistency

The state save/load functions at lines ~25000 and ~25318 use `n_embd_k_gqa + n_embd_k_s` addition formula, which is inconsistent with the exclusive hybrid cache allocation (a layer is either recurrent OR attention, not both). Needs fixing for proper state persistence.

## Previously Fixed Problems

| Problem | Root Cause | Fix |
|---------|-----------|-----|
| KV cache OOM (51200 MiB) | All layers using recurrent state size (`n_embd_k_s * kv_size`) | Per-layer allocation: recurrent layers use rs_size, attention layers use kv_size |
| `wqkv_gate` null pointer | Tensor name mismatch | Renamed to `attn_gate` to match model file |
| `n_seq_tokens == 1` assertion | Worst-case batch had n_tokens=512, violating delta-net constraint | Set n_tokens=1 for hybrid worst-case |
| `ggml_can_repeat` shape mismatch | Gate applied after wo projection (wrong shapes) | Moved gate-before-wo |
| `ggml_get_rows` crash on inference | `cache.recurrent=false` for hybrid → state management never runs | Implemented full hybrid cache with separate `rs_cells[]` |
| Build failure (profiler.h) | Raw cmake missing include paths | Use `scripts/build_cellswarm.sh` |
| `llama_model_is_hybrid` undeclared | Function defined after use | Added forward declaration |
| **Warmup crash: `ggml_assert(view_src)` (THIS SESSION)** | `split_simple` creates virtual per-token sequences, making `ubatch.n_seqs=2` while `rs_n=1` (both virtual seqs map to same seq_id=0). `llm_build_copy_mask_state` computes `n_kv - n_seqs` which underflows. | Hybrid uses `split_equal(1)` + `simple_split=false` in `from_batch`. This ensures each ubatch has 1 real sequence with 1 token (matching delta-net's n_seq_tokens=1 requirement). |
| **Context shift crash** | Server re-evaluates all tokens at once (`n_seq_tokens=32`), violating delta-net constraint | Use larger n_ctx to avoid context shift (permanent fix TBD) |

## Key Fix Details (This Session)

### Root Cause: `split_simple` vs. Hybrid Recurrent State

The crash at `ggml.c:4015` (`view_src` OOB) was caused by a fundamental mismatch in how batches are split for hybrid models:

1. For non-recurrent models, `split_simple` creates "virtual sequences" — each token gets its own virtual sequence, so `ubatch.n_seqs = n_tokens`.
2. For recurrent models, `split_equal` groups tokens by actual sequence, so `ubatch.n_seqs` = number of real sequences.
3. Hybrid models were using `split_simple` (since `cache.recurrent=false`), creating virtual sequences.
4. The hybrid `find_slot` code correctly sets `rs_n` based on unique seq_ids. But when 2 virtual sequences both map to seq_id=0, `rs_min=rs_max=0`, giving `rs_n=1`.
5. The graph builder then passes `n_seqs=2` (from ubatch) but `state_n=rs_n=1` to `llm_build_copy_mask_state`, which computes `n_kv - n_seqs = 1 - 2 = -1` (underflow → huge size), causing the view OOB crash.

### Fix Applied

Two changes in `llama_decode_internal()`:

```cpp
// 1. Don't use simple_split for hybrid models (need real sequence grouping)
lctx.sbatch.from_batch(batch_all, n_embd,
    /* simple_split */ !kv_self.recurrent && !kv_self.hybrid,
    /* logits_all   */ n_outputs == n_tokens_all);

// 2. Use split_equal(1) for hybrid — one token per sequence per ubatch
// This satisfies delta-net's n_seq_tokens == 1 constraint
} else if (kv_self.hybrid) {
    ubatch = lctx.sbatch.split_equal(1);
}
```

## Performance (Host x86_64, 8 threads, CPU-only)

| Metric | Value |
|--------|-------|
| Prompt eval (5 tokens) | 1.54 tok/s |
| Token generation | 1.61 tok/s |
| KV cache size | 450.88 MiB (c=256, np=1) |
| Compute buffer | 40.18 MiB |

## Remaining Work

1. **Fix output quality** — Delta-net state update likely has a bug; output is degenerate (loops/repeats)
2. **Handle context shift** — Either disable for hybrid or implement multi-token delta-net
3. **Test Qwen3.5-35B-A3B** — MoE variant (may need expert routing code)
4. **Fix save/load state** — Inconsistent with exclusive hybrid cache allocation
5. **Find a compatible draft model** — No small Qwen3.5 model exists; Qwen3 0.6B has 151K vocab vs Qwen3.5's 248K vocab (incompatible for speculative decoding)
6. **Deploy and benchmark on phone ring** — Cross-compile, push to phones, run cellswarm-master

## NPU Testing Results (Separate Investigation, Completed)

The Snapdragon 888 Hexagon 780 NPU was tested and found to be **impractical for LLM inference**:
- 26 TOPS theoretical INT8 throughput
- Actual effective: 0.0002–0.023 TOPS (<0.1% utilization)
- ~50ms minimum dispatch overhead per operation
- NPU is slower than CPU at all LLM-relevant matrix sizes except very large FFN (11008×4096)
- Even clustering 20 phones' NPUs (~520 TOPS theoretical) would yield ~0.5 effective TOPS — worse than a single phone's CPU

## Other Completed Investigations

- **KleidiAI**: Only supports Q4_0/Q8_0, not Q4_K_M. No benefit.
- **Qwen2.5-Coder-32B**: 2.85 tok/s on 7 phones (27% slower than DeepSeek-33B due to lower draft acceptance)
- **Flash attention**: Free ~5% decode improvement, enabled in build

## Key Files

| File | Description |
|------|-------------|
| `vendor/cellswarm/src/llama.cpp` | All changes (monolithic file, ~25K lines) |
| `vendor/cellswarm/ggml/src/ggml.c` | View bounds assertion (line ~4015) |
| `scripts/build_cellswarm.sh` | Build script (must use instead of raw cmake) |
| `models/Qwen3.5-27B-Q4_K_M.gguf` | 15.58 GiB dense model (in ~/models/) |
| `models/Qwen3.5-35B-A3B-Q4_K_M.gguf` | ~20 GiB MoE model (in ~/models/) |

## Branch

`feature/qwen35-support` (off `feature/pipeline-optimizations`)
