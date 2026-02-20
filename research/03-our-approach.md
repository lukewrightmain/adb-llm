# 03 — Our Approach: Architecture Evolution

## Timeline

| Date | Milestone | Configuration | Result |
|------|-----------|--------------|--------|
| Feb 14 | llama.cpp RPC | 3 USB phones + host | 0.75 -> 1.08 tok/s |
| Feb 15 | cellswarm ring (non-spec) | 3 USB phones | 1.07 tok/s |
| Feb 15 | cellswarm ring (spec) | 3 USB phones | 1.39 tok/s |
| Feb 16 | Ethernet direct IP | 4-20 Ethernet phones | Bypasses USB tunnel bug |
| Feb 17 | Speculative decoding (Ethernet) | 4-10 Ethernet phones | 2.0-3.1 tok/s |
| **Feb 18** | **Pipeline parallelism** | **10 Ethernet phones** | **3.345 tok/s** |

## Phase 1: llama.cpp RPC (Feb 14)

### Architecture

```
Coding Server (host)              USB Tunnel              Phones (3x)
+------------------+     +-------------------+     +------------------+
| swarm-server     |---->| ADB fwd + SSH -L  |---->| swarm-rpc       |
| (coordinates     |     | Phone:60000 ->    |     | (tensor compute) |
|  tensor splits)  |<----| WinPC -> localhost |<----| (per-layer ops)  |
+------------------+     +-------------------+     +------------------+
```

The host swarm-server splits tensor operations across 3 swarm-rpc instances running on phones. Each phone processes its share of every tensor operation for every layer. The host coordinates all operations, sending `graph_compute` commands and receiving results.

### Optimizations Applied

1. **ARM dotprod + fp16 flags** — Enabled NEON dot product instructions (+15-25%)
2. **LZ4 compression** — Compressed RPC traffic (~600KB/token/phone -> much less)
3. **swarm-rpc hash cache** — Cached model tensors on-phone (reload: 20 min -> 2 min)
4. **graph_recompute cache fix** — Fixed upstream bug: pointer comparison -> topology comparison. Eliminated ~1.8 MB/token serialization overhead. This was the biggest single win.
5. **Batch init_tensor** — Reduced model loading round-trips by 9x

### Result

**0.75 -> 1.08 tok/s (+44%)** on DeepSeek Coder 33B Q4_K_M with 3 phones.

### Why We Moved On

RPC tensor parallelism requires the host to coordinate every tensor operation. Each layer needs a round-trip to all phones. For a 62-layer model with 3 phones, that's ~186 round-trips per token through USB tunnel chains. The communication overhead dominates compute at this latency.

## Phase 2: cellswarm Ring (Feb 15)

### Architecture

```
Phone 0 (rank 0)          Phone 1 (rank 1)         Phone 2 (rank 2)
+-----------------+       +-----------------+       +-----------------+
| Layers 0..20    |--ZMQ->| Layers 21..41   |--ZMQ->| Layers 42..61   |--+
+-----------------+       +-----------------+       +-----------------+  |
       ^                                                                 |
       +-----------------------------------------------------------------+
                              ZMQ ring (TCP)
```

Switched from tensor parallelism to pipeline parallelism using cellswarm. Each phone holds a contiguous slice of transformer layers. Activations flow around the ring — each phone receives, computes its layers, and forwards. One ring traversal = one token's forward pass.

### Key Difference from RPC

| Property | RPC (tensor parallel) | Ring (pipeline parallel) |
|----------|----------------------|-------------------------|
| Communication per layer | Round-trip to all servers | Point-to-point to next rank |
| Round-trips per token | Layers x Servers = 186 | Hops = 3 (one ring traversal) |
| Synchronization | Barrier per layer | None (sequential pipeline) |
| Host required? | Yes (coordinator) | No (phone-only) |

### Result

**1.07 tok/s** (non-speculative, 3-phone ring, Q4_K_M)

### Speculative Decoding Addition

Added a 1.3B draft model on rank 0:

```
Phone 0 (rank 0)
+-----------------+
| Draft: 1.3B     | <- Generates 8 candidate tokens (~300ms)
| Target: Layers  | <- Verifies candidates through ring
| 0..20           |
+-----------------+
```

**1.39 tok/s** (speculative, 3-phone ring, 56.7% acceptance)

### The USB Tunnel Bug

Attempts to scale beyond 3 phones failed. The 4-phone ring crashes during the ZMQ bcast startup handshake — rank 2's message to rank 3 is lost in the ADB/SSH tunnel chain. The tunnel architecture requires:

```
Phone -> ADB reverse -> WinPC -> SSH tunnel -> Coding Server -> SSH tunnel -> WinPC -> ADB forward -> Phone
```

This 4-hop path works for 3 phones but fails for 4+. Root cause never fully determined (suspected ZMQ PUSH buffering vs async tunnel establishment).

## Phase 3: Ethernet Direct IP (Feb 16)

### Architecture Change

Replaced USB tunnel chains with direct Ethernet IP communication:

```
Phone 0 (10.105.0.12)   Phone 1 (10.105.0.13)   Phone N (10.105.0.xx)
+------------------+     +------------------+     +------------------+
| Layers 0..K      |---->| Layers K+1..M    |---->| Layers ...62     |--+
| tcp://10.105.0.13 |    | tcp://10.105.0.xx |    | tcp://10.105.0.12 | |
+------------------+     +------------------+     +------------------+ |
       ^                                                                |
       +----------------------------------------------------------------+
                    Direct Ethernet (1-3ms per hop)
```

No tunnels. No relay through WinPC or coding server. Phones communicate directly at their Ethernet IPs. This bypasses the 4-phone tunnel bug entirely and reduces per-hop latency from 5-10ms to 1-3ms.

### Result

4-phone ring works. 10-phone ring works. 20-phone ring works. Scale unlocked.

## Phase 4: Pipeline Parallelism (Feb 18)

### The Sequential Ring Problem

Without pipeline parallelism, speculative verification is sequential:

```
Token 1: [Phone 0] -> [Phone 1] -> ... -> [Phone 9] -> [Phone 0]  (~1000ms)
Token 2:                                                 [Phone 0] -> [Phone 1] -> ...  (~1000ms)
...
Token 9:                                                                                  (~1000ms)
Total: 9 x 1000ms = 9000ms for 9 tokens
```

Each token must complete the entire ring before the next starts. Total verification time = tokens x ring_latency. Adding phones doesn't help because it doesn't reduce the ring traversal time (it reduces per-phone compute but adds per-hop communication).

This is why the non-speculative ring gives exactly **1.04 tok/s regardless of phone count**.

### Pipeline Solution

`llama_decode_pipeline()` sends all verification tokens through the ring simultaneously:

```
Time ->
Phone 0:  [Send T1][Send T2][Send T3]...[Recv T1][Recv T2]...[Recv T9]
Phone 1:           [Comp T1][Comp T2][Comp T3]...
Phone 2:                    [Comp T1][Comp T2][Comp T3]...
...
Phone 9:                                        [Comp T1][Comp T2]...
```

- **Phase 1 (Send):** Rank 0 sends all 9 token embeddings into the ring back-to-back (~1ms)
- **Overlap:** While phone 1 processes token 2, phone 2 processes token 1, phone 3 processes the previous token, etc.
- **Phase 2 (Recv):** Rank 0 receives completed tokens as they exit the ring

**Pipeline timing (10 phones):**
- First result: ~1,000ms (full ring latency)
- Subsequent results: every 20-30ms (pipeline drain)
- Total for 9 tokens: ~1,840ms (vs ~9,000ms sequential)

This transforms verification from `N_tokens x ring_latency` to `ring_latency + (N_tokens - 1) x per_rank_compute`.

### Result

**3.345 tok/s** on 10 phones — a 3.2x improvement over the non-speculative baseline.

## Critical Flags

| Flag | Purpose | Impact if Missing |
|------|---------|-------------------|
| `--no-mmap` | Explicit memory loading | 87x slowdown (mmap page thrashing) |
| `--prefetch` | Prefetch model weights | ~5-10% throughput loss |
| `taskset f0` | Pin to big cores 4-7 | Compute spills to little cores |
| `-t 4` | 4 threads (matches big cores) | Extra threads hurt (little core contention) |
| `-c 512` | Context size | Larger context uses more memory |
| `--draft-max 8` | Speculative candidates | Fewer = less amortization, more = diminishing returns |
| `-lw 5,7,7,...` | Layer weight distribution | Unbalanced = pipeline bubbles |

## Speculative Decoding Cycle

One complete cycle at 10 phones:

```
1. KV management           ~0ms    Bookkeeping
2. Draft resync            ~38ms   Sync draft model with target state
3. Draft loop             ~305ms   Generate 8 tokens with 1.3B model (on-phone)
4. Verify KV ops           ~0ms    Prepare KV cache for verification
5. Pipeline verify       ~1840ms   Send 9 tokens through 10-phone ring
                        --------
   Total cycle          ~2145ms   Produces ~6 accepted tokens (77.8% rate)

Effective throughput: 6 tokens / 2145ms = 2.80 tok/s (measured: 3.345 with overhead amortization)
```
