# 01 — Hardware Platform

## SoC: Qualcomm Snapdragon 888 (SM8350)

| Attribute | Value |
|-----------|-------|
| CPU | Kryo 680: 1x Cortex-X1 @ 2.84 GHz + 3x Cortex-A78 @ 2.42 GHz + 4x Cortex-A55 @ 1.80 GHz |
| Architecture | ARMv8.4-A (no i8mm — requires ARMv8.6+) |
| SIMD | NEON with dotprod + fp16 |
| GPU | Adreno 660 (non-functional for LLM compute — see [Lessons Learned](07-lessons-learned.md)) |
| Process | Samsung 5nm |
| RAM (total) | ~11.3-11.6 GB (LPDDR5) |
| RAM (available) | ~5.0-5.7 GB per phone (after Android OS) |

### ARM ISA Extensions

Compilation flags: `-march=armv8.2-a+dotprod+fp16`

- `dotprod` — 8-bit dot product instructions, critical for Q4_K_M matrix multiplications (~15-25% speedup over baseline ARMv8.0)
- `fp16` — Half-precision floating point, used in dequantization paths
- `i8mm` — **NOT AVAILABLE**. Snapdragon 888 is ARMv8.4; i8mm requires ARMv8.6+. Building with `+i8mm` causes SIGILL crashes at runtime.

### GPU Status (Adreno 660)

Tested February 2026. Both GPU compute paths fail:

- **OpenCL:** Kernel compiler crashes (driver date 09/2023, too old for llama.cpp compute kernels)
- **Vulkan:** Initializes successfully (Vulkan 1.1), but `vk::DeviceLostError` on compute shader dispatch. Even `ngl=1` crashes. BLAS-via-Vulkan benchmarked 2x slower than NEON CPU before crashing.

GPU acceleration requires Adreno 750+ (Snapdragon 8 Gen 3 or newer).

## Phone: Samsung Galaxy Z Fold3 (SM-F926U/U1/W)

| Attribute | Value |
|-----------|-------|
| Model | SM-F926U (T-Mobile), SM-F926U1 (unlocked), SM-F926W (Canadian) |
| SoC | Snapdragon 888 |
| Storage | 256 GB (218 GB free typical) |
| OS (USB fleet) | Android 14 |
| OS (Ethernet fleet) | Android 15 |
| Form factor | Foldable (used closed, outer screen only) |

## Fleet Inventory

### USB-Connected Phones (20 devices)

Connected via USB cables to a Windows PC (`winpc` / 10.69.1.114). ADB access relayed through SSH from the coding server.

Serial numbers:
```
R3CR904AQKA  R3CR90AJCPF  R3CRA0KPK9M  R3CRB0726FZ  R3CRC0697RY
R3CRC071RZH  R3CRC085GGE  R3CRC09292M  R3CRC0MVVDN  R3CT40EHA2B
R3CT40TJY8T  R3CT505TXMW  R3CT508XJRF  R3CT60BAJXK  RFCR70TXFXJ
RFCR71N4AFH  RFCR80H5CRZ  RFCRA19TM7Y  RFCRB0B1N6Y  RFCRB0BYFAX
```

USB phones require ADB tunnel chains for phone-to-phone communication:
```
Phone:60000 -> ADB forward -> WinPC:600XX -> SSH tunnel -> localhost:600XX
```

**Limitation:** USB tunnel chain fails at 4+ phones due to a ZMQ buffering/tunnel interaction bug (see [Lessons Learned](07-lessons-learned.md)). Only 3-phone rings work over USB.

### Ethernet-Connected Phones (20 devices)

Connected via USB-C Ethernet adapters through a network switch. ADB over TCP/IP at 10.105.0.x:5555.

| IP Address | Model |
|------------|-------|
| 10.105.0.12 | SM-F926W |
| 10.105.0.13 | SM-F926U |
| 10.105.0.17 | SM-F926U |
| 10.105.0.19 | SM-F926U |
| 10.105.0.20 | SM-F926U |
| 10.105.0.24 | SM-F926U |
| 10.105.0.28 | SM-F926U |
| 10.105.0.29 | SM-F926U1 |
| 10.105.0.30 | SM-F926U |
| 10.105.0.31 | SM-F926U |
| 10.105.0.32 | SM-F926U |
| 10.105.0.36 | SM-F926U |
| 10.105.0.38 | SM-F926U |
| 10.105.0.40 | SM-F926U |
| 10.105.0.41 | SM-F926U |
| 10.105.0.42 | SM-F926U |
| 10.105.0.44 | SM-F926U |
| 10.105.0.45 | SM-F926U1 |
| 10.105.0.48 | SM-F926U |
| 10.105.0.156 | SM-F926U |

Ethernet phones communicate directly via IP — no tunnels, no relay. Phone-to-phone latency: 1-3ms. This bypasses the USB tunnel bug entirely and enables 4-20 phone rings.

### Total Fleet

**40 Samsung Galaxy Z Fold3 phones** (20 USB + 20 Ethernet).

## Network Configurations Tested

| Network | Max Ring Size | Latency (hop) | Status |
|---------|--------------|----------------|--------|
| WiFi 6E (exo project) | 9 phones | 10-30ms, unstable | Abandoned — connections cycle, BrokenPipe errors |
| USB + ADB tunnels | 3 phones | 5-10ms | Works for 3, fails at 4+ (tunnel bug) |
| **Ethernet (direct IP)** | **20 phones** | **1-3ms** | **Production — all scaling results use this** |

## Thread Pinning

Snapdragon 888 has 8 cores in three clusters:

| Cluster | Cores | Frequency | Used? |
|---------|-------|-----------|-------|
| Cortex-X1 (prime) | Core 7 | 2.84 GHz | Yes (via taskset) |
| Cortex-A78 (big) | Cores 4-6 | 2.42 GHz | Yes (via taskset) |
| Cortex-A55 (little) | Cores 0-3 | 1.80 GHz | No |

Thread pinning command: `taskset f0` (hex mask for cores 4-7).

We use `-t 4` (4 threads) to match the 4 high-performance cores. Using more threads spills onto little cores and reduces throughput.

## On-Phone Directory Layout

```
/data/local/tmp/cellswarm/
  bin/
    cellswarm-worker           # Ring worker binary (40 MB, ARM64)
    cellswarm-worker-spec      # Speculative ring worker, rank 0 only (40 MB)
  models/
    deepseek-coder-33b-instruct.Q4_K_M.gguf   # Target model, all phones (18.6 GiB)
    deepseek-coder-1.3b-instruct.Q4_K_M.gguf  # Draft model, rank 0 only (832 MiB)
```
