# 06 — Comparison Analysis

## Master Comparison Table

All results normalized where possible. "Effective model" accounts for quantization — a 33B Q4_K_M model has similar quality to a 33B FP16 but uses ~4 bits/param.

| Project | Model | Size | Quant | Devices | GPU? | Network | tok/s | tok/s per device |
|---------|-------|------|-------|---------|------|---------|-------|-----------------|
| **Ours (peak)** | **DeepSeek 33B** | **18.6 GiB** | **Q4_K_M** | **10 phones** | **No** | **Ethernet** | **3.345** | **0.33** |
| Ours (non-spec) | DeepSeek 33B | 18.6 GiB | Q4_K_M | 10 phones | No | Ethernet | 1.04 | 0.10 |
| Ours (RPC) | DeepSeek 33B | 18.6 GiB | Q4_K_M | 3 phones+host | No | USB tunnel | 1.08 | 0.27 |
| cellswarm official | DeepSeek 32B | ~17 GiB | Q4_K_S | Mixed cluster | Yes | InfiniBand | ~11.2 | — |
| cellswarm official | LLaMA 70B | ~37 GiB | Q4_K_S | Mixed cluster | Yes | InfiniBand | ~1.48 | — |
| RPi CM5 cluster | LLaMA 70B | ~37 GiB | Q4_0 | 10 RPi | No | Gigabit Eth | ~0.85 | 0.085 |
| Petals | LLaMA 2 70B | ~140 GiB | FP16/INT8 | GPU swarm | Yes | Internet | ~6 | — |
| PowerInfer-2 | LLaMA 7B | ~4 GiB | Mixed | 1 phone | Yes | N/A | ~11 | 11.0 |
| llama.cpp (A100) | LLaMA 70B | ~37 GiB | Q4_K_M | 1 GPU | Yes | N/A | ~45 | 45.0 |
| llama.cpp (M2 Ultra) | LLaMA 70B | ~37 GiB | Q4_K_M | 1 SoC | Yes | N/A | ~11 | 11.0 |

## Scaling Efficiency

### Throughput per Phone

| Phones | tok/s | tok/s per phone | Efficiency vs 4-phone |
|--------|-------|----------------|----------------------|
| 4 | 2.006 | 0.502 | 100% (baseline) |
| 6 | 2.144 | 0.357 | 71% |
| 8 | 3.130 | 0.391 | 78% |
| **10** | **3.345** | **0.335** | **67%** |
| 12 | 2.854 | 0.238 | 47% |
| 15 | 2.437 | 0.162 | 32% |
| 20 | 1.868 | 0.093 | 19% |

Per-phone efficiency peaks at 4 phones but total throughput peaks at 10. The optimal operating point depends on whether you optimize for throughput (10 phones) or per-device efficiency (4 phones).

### Scaling Curve with Per-Phone Efficiency

```
  tok/s                                  efficiency
  3.5 |              *                   | 100%
  3.0 |         *                        |  80%
      |                    *             |  60%
  2.5 |                         *        |  40%
  2.0 |  *      *                        |  20%
      |                              *   |
  1.5 |                                  |
  1.0 |  ---- non-spec baseline ----     |   0%
      +-----+----+----+----+----+----->  +-------->
        4    6    8   10   12   20         phones

  Total tok/s: solid line peaks at 10
  Per-phone:   dotted line decreases monotonically
```

## Cost Analysis

### Hardware Cost

| System | Hardware | Cost (USD) | tok/s | $/tok/s |
|--------|----------|-----------|-------|---------|
| **10 phones (ours)** | 10x Z Fold3 + Ethernet adapters + switch | ~$2,200 | 3.345 | **$658** |
| 40 phones (full fleet) | 40x Z Fold3 + networking | ~$8,400 | 3.345 (10 active) | $2,512 |
| 10x RPi CM5 | 10x CM5 8GB + networking | ~$1,200 | ~0.85 | $1,412 |
| NVIDIA A100 80GB | 1x A100 + server | ~$15,000 | ~45 | $333 |
| Apple M2 Ultra | 1x Mac Studio | ~$6,000 | ~11 | $545 |

**Cost breakdown for 10-phone cluster:**
- 10x Samsung Galaxy Z Fold3 (used, ~$180/ea): $1,800
- 10x USB-C Ethernet adapter (~$15/ea): $150
- 1x Gigabit Ethernet switch: $30
- 1x Ethernet cables: $20
- **Total: ~$2,000**

### Price per tok/s

At used phone prices (~$180/phone), our cluster is competitive with enterprise hardware on a $/tok/s basis:

| System | $/tok/s |
|--------|---------|
| A100 | $333 |
| **10 phones (ours)** | **$598** |
| M2 Ultra | $545 |
| 10x RPi CM5 | $1,412 |

The A100 wins on raw $/tok/s, but the phone cluster has advantages in power consumption and accessibility.

### Power Consumption

| System | Power Draw | tok/s | tok/s/Watt |
|--------|-----------|-------|------------|
| **10 phones** | **~30W** (3W/phone) | **3.345** | **0.112** |
| 10x RPi CM5 | ~50W (5W/board) | ~0.85 | 0.017 |
| A100 server | ~400W (GPU + host) | ~45 | 0.113 |
| M2 Ultra | ~120W | ~11 | 0.092 |

Our phone cluster matches the A100 on tok/s/Watt — a remarkable result given the 13x throughput difference. This is because ARM big.LITTLE cores are extremely power-efficient, and phones are designed for battery operation.

### Energy Cost per Token

At $0.12/kWh (US average):

| System | Watts | tok/s | Energy per 1000 tokens | Cost per 1000 tokens |
|--------|-------|-------|----------------------|---------------------|
| **10 phones** | 30 | 3.345 | 2.49 Wh | **$0.000299** |
| A100 server | 400 | 45 | 2.47 Wh | $0.000296 |
| 10x RPi | 50 | 0.85 | 16.3 Wh | $0.00196 |
| M2 Ultra | 120 | 11 | 3.03 Wh | $0.000364 |

Our phone cluster and the A100 are nearly identical in energy cost per token. The RPi cluster is 6.5x more expensive per token in energy alone.

## Model Size Scaling

Our cluster currently runs 33B. Theoretical limits:

| Model | Size (Q4_K_M) | Phones Needed | Feasible? |
|-------|--------------|---------------|-----------|
| 7B | ~4 GiB | 1-2 | Yes (but better on single device) |
| 13B | ~8 GiB | 2-3 | Yes |
| **33B** | **18.6 GiB** | **4-10** | **Yes (current)** |
| 70B | ~37 GiB | 8-20 | Yes (with ~5GB/phone) |
| 120B | ~65 GiB | 14-30 | Possible (tight on memory) |

The 70B model would need ~8 phones minimum to fit in memory, and the optimal configuration would likely be 15-20 phones (smaller per-phone slice). Based on our scaling curve, we'd expect ~2-3 tok/s at the sweet spot.

## Advantages of Phone Clusters

| Advantage | Detail |
|-----------|--------|
| Cost | Used flagship phones are cheap ($150-200) and getting cheaper |
| Power | 3W/phone, runs on USB power, no cooling required |
| Availability | Millions of used phones available on secondary market |
| Portability | Entire cluster fits in a backpack |
| Silence | No fans, no noise |
| Scalability | Add/remove phones dynamically (with appropriate software) |

## Disadvantages of Phone Clusters

| Disadvantage | Detail |
|--------------|--------|
| Throughput ceiling | ~3.5 tok/s for 33B is adequate for single-user, not for serving |
| Network dependency | Ethernet required for stability; WiFi too unreliable |
| No GPU | Snapdragon 888's Adreno 660 is non-functional for LLM compute |
| Software maturity | Custom cellswarm fork, not upstream |
| Memory limits | ~5 GiB/phone limits model size without more phones |
| Maintenance | 40 physical devices to manage, charge, update |
