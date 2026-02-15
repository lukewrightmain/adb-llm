# Optimize RPC Inference: 0.75 → 1.08 tok/s (+44%)

**Branch:** `optimize-rpc-inference` (both `adb-llm` and `llama.cpp` repos)
**Date:** 2026-02-14
**Model:** DeepSeek Coder 33B Q4_K_M (19GB) on 3 Samsung Galaxy Z Fold3 (Snapdragon 888) via USB

## Results

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Generation speed | 0.75 tok/s | 1.08 tok/s | **+44%** |
| Model load (first) | ~20 min | ~11 min | -45% |
| Model load (cached) | ~20 min | ~2 min | **-90%** |

## What Changed

### 1. ARM dotprod + fp16 instructions (device-specific)

**File:** `scripts/build_rpc_server.sh`
**Scope:** ARMv8.2+ devices (Snapdragon 888, 8 Gen 1/2/3, Dimensity 9000+, etc.)

Added `-DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16` to the Android rpc-server cmake build. This enables NEON dot product instructions that accelerate Q4_K_M matrix multiplications. Previously the build used the default ARMv8.0 instruction set, leaving performance on the table.

**Impact:** ~15-25% faster compute on the phones.

### 2. LZ4 compression for RPC traffic (universal)

**Files:** `scripts/build_rpc_server.sh`, `src/adb_llm/inference/server_manager.py`
**Scope:** Any llama.cpp RPC setup

The RPC protocol supports optional LZ4 compression (env var `GGML_RPC_COMPRESS=1`), but it was never compiled into either the host llama-server or the Android rpc-server because `liblz4-dev` wasn't installed and no LZ4 was cross-compiled for Android.

Changes:
- **Build script:** Cross-compiles LZ4 v1.10.0 as a static library using the NDK, passes it to cmake via `-DLZ4_LIBRARIES` and `-DLZ4_INCLUDE_DIRS`
- **Host:** Installed `liblz4-dev`, rebuilt llama-server (now dynamically links `liblz4.so.1`)
- **server_manager.py:** Passes `GGML_RPC_COMPRESS=1` environment variable to llama-server subprocess

This compresses the `graph_compute` payload (~600KB per phone per token) and `SET_TENSOR` data during model loading.

**Impact:** Reduced per-token network transfer, faster model loading.

### 3. rpc-server hash cache for model loading (universal)

**File:** `src/adb_llm/inference/rpc_manager.py`
**Scope:** Any llama.cpp RPC setup

The rpc-server supports `--cache` which creates a file-based cache. On subsequent model loads, `SET_TENSOR_HASH` finds cached tensor data and skips the transfer entirely. This was never enabled because:
- The `--cache` flag wasn't passed
- On Android, `$HOME` is unset so the default cache path fails

Changes:
- Sets `LLAMA_CACHE=/data/local/tmp/adb-llm/cache` before launching rpc-server
- Creates the cache directory
- Passes `--cache` flag

**Impact:** Model reload drops from ~20 min to ~2 min (hash hits skip 19GB of transfers).

### 4. Fix graph_recompute cache — THE BIG WIN (universal, bug fix)

**File:** `llama.cpp/ggml/src/ggml-rpc/ggml-rpc.cpp` (lines 262-306)
**Scope:** **All llama.cpp RPC users** — this is a bug fix in upstream code

The RPC protocol has a `GRAPH_RECOMPUTE` optimization: if the compute graph hasn't changed since the last token, send a 4-byte "recompute" message instead of re-serializing the entire graph (~600KB per phone). The existing cache check used `memcmp` on the full `ggml_tensor` struct, which includes pointer fields (`data`, `buffer`, `src[]`). These pointers change between tokens even though the graph topology is identical, so **the cache never hit during generation**.

Changed `graph_cache::is_cached()` to compare only topology-relevant fields:
- `op`, `type`, `flags`
- `ne[0..3]`, `nb[0..3]` (shape and strides)
- `op_params` (operation parameters)
- Source existence (which `src[]` slots are non-null, not their pointer values)

This makes `GRAPH_RECOMPUTE` hit on every generation token after the first, eliminating ~1.8MB of serialization per token across 3 phones (replaced by 3 x 4-byte messages).

**Impact:** Eliminates the dominant per-token network overhead. This is the single biggest contributor to the speedup.

### 5. Batch init_tensor for model loading (universal)

**File:** `llama.cpp/ggml/src/ggml-rpc/ggml-rpc.cpp`
**Scope:** All llama.cpp RPC users

During model loading, `init_tensor` is called individually for each quantized tensor needing padding (ne[0] % 512 != 0). Each call is a synchronous roundtrip. Added batching:

- New `RPC_CMD_INIT_TENSORS_BATCH` command
- Client queues up to 64 init_tensor requests, sends them as a single message
- Flush triggers automatically at batch size 64, or before any `set_tensor`/`get_tensor` call
- Server-side handler iterates the batch and calls `init_tensor` for each

**Impact:** Reduces model loading roundtrips from N to N/64 (~9x fewer for the init phase).

## Files Modified

### adb-llm repo

| File | Change |
|------|--------|
| `scripts/build_rpc_server.sh` | ARM dotprod+fp16 flag, LZ4 cross-compilation |
| `src/adb_llm/inference/server_manager.py` | `GGML_RPC_COMPRESS=1` env var |
| `src/adb_llm/inference/rpc_manager.py` | `--cache` flag, `LLAMA_CACHE` env var |

### llama.cpp repo

| File | Change |
|------|--------|
| `ggml/src/ggml-rpc/ggml-rpc.cpp` | Topology-only graph cache, batch init_tensor (client + server) |

## Verification Commands

```bash
# Verify ARM dotprod in Android binary
readelf -A bin/rpc-server | grep -i dot

# Verify LZ4 in Android binary (static)
nm bin/rpc-server | grep -i lz4

# Verify LZ4 in host llama-server (dynamic)
ldd ~/llama.cpp/build/bin/llama-server | grep lz4

# Verify cache dir on phone
ssh winpc 'adb -s <serial> shell ls /data/local/tmp/adb-llm/cache/'

# Verify graph_recompute hits (debug mode)
GGML_RPC_DEBUG=1 GGML_RPC_COMPRESS=1 llama-server --model ... --rpc ...
# Look for "graph_recompute" log lines after first generation token
```

## What's NOT Device-Specific

4 out of 5 changes are universal RPC improvements that benefit any llama.cpp RPC deployment — phones, Raspberry Pis, remote GPUs, anything. The graph_recompute fix (Step 4) is arguably a bug that should be upstreamed to llama.cpp.

Only Step 1 (ARM dotprod+fp16) is architecture-specific, and even that applies to any ARMv8.2+ device, not just Snapdragon 888.
