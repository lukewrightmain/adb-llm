# Investigating the 26ms ZMQ Hop Overhead

**Branch:** `zmq-hop-investigation`
**Goal:** Profile and reduce the ~26ms per-hop overhead in the ZMQ ring pipeline.

---

## Background

Each ring hop takes ~86ms: 60ms compute + 26ms overhead. The payload is ~28.7KB per hop (hidden_dim 7168 x 4 bytes FP32 + 24-byte packed header). Over ethernet at 1Gbps, wire time is ~0.23ms. Network RTT is 2-4ms. So **~22ms is pure software overhead in ZMQ**.

### Current pipeline performance (12 phones, d24)

| Phase | Time |
|-------|------|
| Hop compute (5-6 layers x 12ms) | 60ms |
| Hop overhead | **26ms** |
| **Total per hop** | **86ms** |
| Pipeline drain (35 hops) | 3010ms |
| Full cycle | 3090ms |
| **Throughput** | **6.1 tok/s** |

### If hop drops to 6ms (raw TCP estimate)

| Phase | Time |
|-------|------|
| Hop compute | 60ms |
| Hop overhead | **6ms** |
| **Total per hop** | **66ms** |
| Pipeline drain (35 hops) | 2310ms |
| Full cycle | 2347ms |
| **Throughput** | **~8.1 tok/s (+32%)** |

---

## Hypothesis: Where the 22ms Goes

| Component | Est. Time | Why |
|-----------|-----------|-----|
| ZMQ message construction | 1-2ms | zmq_msg_init, internal framing |
| ZMQ send → I/O thread handoff | 2-4ms | Lock contention, thread signaling, context switch |
| Kernel syscall (send/write) | 1-2ms | write() to TCP socket |
| Network transit | 2-4ms | Measured RTT between phones |
| Kernel syscall (recv/read) | 1-2ms | read() from TCP socket |
| ZMQ I/O thread → app thread handoff | 2-4ms | Same signaling overhead on receive side |
| ZMQ poll/wake latency | 3-5ms | zmq_poll has minimum wake granularity |
| Buffer copies | 1-2ms | ZMQ → app buffer |

**Key suspect:** ZMQ's I/O threading model. ZMQ uses background I/O threads that handle actual socket operations, then signal the application thread via an inproc pipe. That's two context switches per message (send side + recv side) plus poll granularity.

---

## Investigation Tools

### 1. ZMQ Hop Profiling (PRIMA_HOP_PROFILE=1)

Microsecond-resolution timestamps inside `llama_send_tensors()` and `llama_recv_tensors()` that break down each hop into:

**Send side:**
- `msg_alloc` — Time to create `zmq::message_t(total_bytes)` (includes malloc)
- `pack` — Time to copy header + tensor data + positions into the message
- `zmq_send` — Time inside `socket.send()` (handoff to I/O thread + TCP write)

**Recv side:**
- `zmq_recv` — Time inside `socket.recv()` (I/O thread wake + TCP read + handoff)
- `unpack` — Time to copy data from message to application buffers

**Usage:**
```bash
# Enable on all phones via env var
PRIMA_HOP_PROFILE=1 taskset f0 ./bin/prima-worker-spec --rank 0 ...

# Or use the benchmark script:
./scripts/bench_hop_profile.sh 4
```

**Output format:**
```
[HOP-PROFILE] SEND total=XXXus msg_alloc=XXus pack=XXus zmq_send=XXXus bytes=28696
[HOP-PROFILE] RECV total=XXXus zmq_recv=XXXus unpack=XXus bytes=28696
```

### 2. Raw TCP Hop Benchmark (tcp_hop_bench)

Standalone C program that sends/receives the same 28.7KB payload over raw TCP (no ZMQ) to measure theoretical minimum hop time.

**Modes:**
- **Client/Server:** Measures send time, recv time, and full RTT between two phones
- **Ring:** Simulates a ring hop (recv from prev, forward to next)

**Build and deploy:**
```bash
./scripts/build_tcp_bench.sh
for ip in 10.105.0.{12,13,17}; do
    adb -s $ip:5555 push bin/tcp_hop_bench /data/local/tmp/adb-llm/bin/
done
```

**Usage:**
```bash
# Phone A (server):
ssh root@10.105.0.12 -p 8022 \
  "/data/local/tmp/adb-llm/bin/tcp_hop_bench -s -p 9000 -n 1000"

# Phone B (client):
ssh root@10.105.0.13 -p 8022 \
  "/data/local/tmp/adb-llm/bin/tcp_hop_bench -c 10.105.0.12 -p 9000 -n 1000"
```

**Expected output:**
```
Send (write syscall) (n=1000):
  mean=XXX.X us  p50=XXX us  p95=XXX us  p99=XXX us
Full RTT (send + remote recv + ACK) (n=1000):
  mean=XXXX.X us  p50=XXXX us  p95=XXXX us  p99=XXXX us
```

### 3. Combined Benchmark Script

```bash
# Profile ZMQ hops on 4 phones
./scripts/bench_hop_profile.sh 4

# Profile ZMQ hops + run raw TCP comparison
./scripts/bench_hop_profile.sh 4 --raw-tcp
```

---

## Expected Findings

### Scenario A: ZMQ poll/wake is the bottleneck (most likely)

If `zmq_recv` shows 15-20ms consistently while raw TCP shows 3-5ms, the fix is to eliminate ZMQ's I/O thread hop. Options:
1. **Set ZMQ_IMMEDIATE + single I/O thread** — Reduce context switches
2. **Use ZMQ direct mode** — `ZMQ_ROUTER_RAW` to bypass message framing
3. **Replace ZMQ with raw TCP** — Eliminate ZMQ entirely for the data path

### Scenario B: Kernel/network overhead dominates

If raw TCP also shows 15-20ms, the bottleneck is in the kernel TCP stack or network. Options:
1. **Use larger send/recv buffers** (setsockopt SO_SNDBUF/SO_RCVBUF)
2. **Check TCP_NODELAY** (should already be set)
3. **Try UDP** for the data path (no retransmission needed on local ethernet)

### Scenario C: Message construction overhead

If `msg_alloc` or `pack` shows >5ms, the fix is to pre-allocate and reuse message buffers. Options:
1. **Pre-allocate a persistent send buffer** (already partially done with `ring_send_buf`)
2. **Use `zmq_msg_init_data()` with zero-copy** — Avoid malloc+memcpy on each send
3. **Double-buffer** — Write to buffer A while buffer B is in flight

---

## Next Steps After Profiling

1. **Run `bench_hop_profile.sh 4` on 4 ethernet phones** — Get baseline ZMQ numbers
2. **Run `bench_hop_profile.sh 4 --raw-tcp`** — Get raw TCP comparison
3. **Analyze results** — Determine which scenario (A/B/C) matches
4. **Implement fix** — Based on findings, choose the appropriate optimization
5. **Benchmark end-to-end** — Run full speculative pipeline benchmark to measure tok/s improvement

### If replacing ZMQ with raw TCP (Scenario A fix):

The replacement is scoped to the hot path only:
- `llama_send_tensors()` — Replace `socket.send(msg)` with `write(fd, buf, n)`
- `llama_recv_tensors()` — Replace `socket.recv(msg)` with `read(fd, buf, n)`
- `llama_init_sockets()` — Add raw TCP socket setup alongside ZMQ
- Keep ZMQ for control plane (bcast_startup_args, gather_device_info, etc.)

Estimated effort: ~200 lines of C++ changes. The fixed-size messages (28.7KB) make raw TCP trivial — no framing protocol needed.

---

## Files in This Branch

| File | Purpose |
|------|---------|
| `vendor/prima.cpp/src/llama.cpp` | Instrumented send/recv with `[HOP-PROFILE]` timestamps |
| `benchmarks/tcp_hop_bench.c` | Standalone raw TCP hop latency benchmark |
| `scripts/build_tcp_bench.sh` | Build tcp_hop_bench for ARM64 + x86_64 |
| `scripts/bench_hop_profile.sh` | Run ZMQ profiling + raw TCP comparison on phones |
| `docs/hop-investigation.md` | This document |
