#!/usr/bin/env python3
"""
Phone Calculator — Determine optimal phone count for distributed LLM inference.

Calculates minimum phones (RAM constraint), optimal phone count (throughput-
maximizing), layer distribution, and estimated tok/s for a given model config
running on our prima.cpp fork with pipeline parallelism and speculative decoding.

Usage:
    python3 scripts/phone_calculator.py --layers 62 --model-size 18.6 --ram 5.0
    python3 scripts/phone_calculator.py --layers 32 --model-size 4.0 --ram 5.0 --no-spec
    python3 scripts/phone_calculator.py --layers 62 --model-size 18.6 --ram 5.0 --sweep
"""

import argparse
import math
import sys


def calculate_min_phones(model_size_gb, available_ram_gb, draft_size_gb, speculative):
    """Calculate minimum phones needed based on RAM constraint."""
    if speculative:
        # Rank 0 needs room for both target layers + full draft model
        # Other ranks only need target layers
        # We solve: model_size / n_phones <= available_ram for all ranks
        #           model_size / n_phones + draft_size <= available_ram for rank 0
        # The binding constraint is rank 0:
        #   model_size / n + draft_size <= available_ram
        #   model_size / n <= available_ram - draft_size
        #   n >= model_size / (available_ram - draft_size)
        rank0_budget = available_ram_gb - draft_size_gb
        if rank0_budget <= 0:
            print(f"ERROR: Draft model ({draft_size_gb:.1f} GB) >= available RAM "
                  f"({available_ram_gb:.1f} GB). Cannot fit draft model.", file=sys.stderr)
            sys.exit(1)
        return math.ceil(model_size_gb / rank0_budget)
    else:
        return math.ceil(model_size_gb / available_ram_gb)


def calculate_layer_distribution(total_layers, n_phones, speculative, rank0_reduction=6):
    """Calculate layer assignment per rank.

    With speculative decoding, rank 0 gets fewer target layers because it also
    runs the draft model. This balances compute time across ranks.

    Returns list of layer counts per rank.
    """
    if speculative and n_phones > 1:
        # Reduce rank 0 layers to account for draft model compute overhead
        reduction = min(rank0_reduction, total_layers // n_phones - 1)
        reduction = max(0, reduction)
        remaining = total_layers - (total_layers // n_phones - reduction)
        rank0_layers = total_layers // n_phones - reduction

        # Distribute remaining layers across other ranks
        other_ranks = n_phones - 1
        base = remaining // other_ranks
        extra = remaining % other_ranks

        layers = [rank0_layers]
        for i in range(other_ranks):
            layers.append(base + (1 if i < extra else 0))
    else:
        base = total_layers // n_phones
        extra = total_layers % n_phones
        layers = [base + (1 if i < extra else 0) for i in range(n_phones)]

    return layers


def calculate_throughput(n_phones, total_layers, per_layer_ms, network_overhead_ms,
                         draft_tokens, acceptance_rate, draft_per_token_ms,
                         resync_overhead_ms, speculative, rank0_reduction=6):
    """Calculate estimated tok/s for a given phone count.

    Returns (tok_per_sec, cycle_time_ms, breakdown_dict).
    """
    layers = calculate_layer_distribution(total_layers, n_phones, speculative, rank0_reduction)

    if not speculative:
        # Non-speculative: sequential ring, one token at a time
        # Total latency = sum of (layers_per_rank * per_layer_ms + network_overhead)
        ring_latency_ms = sum(l * per_layer_ms + network_overhead_ms for l in layers)
        tok_per_sec = 1000.0 / ring_latency_ms
        return tok_per_sec, ring_latency_ms, {
            "mode": "sequential (no speculative)",
            "ring_latency_ms": ring_latency_ms,
            "tokens_per_cycle": 1,
        }

    # Speculative with interleaved pipeline
    max_layers_per_rank = max(layers)
    hop_time_ms = max_layers_per_rank * per_layer_ms + network_overhead_ms

    # Full ring traversal for the first token
    first_ring_ms = n_phones * hop_time_ms

    # Draft generation time (interleaved — draft tokens sent as generated)
    draft_time_ms = draft_tokens * draft_per_token_ms

    # Phase 1: draft + send (interleaved, so time = max of draft and first ring)
    # In practice, drafting happens concurrently with tokens flowing through ring.
    # The first token enters the ring immediately; by the time we finish drafting,
    # many tokens are already partway through.
    phase1_ms = max(draft_time_ms, first_ring_ms)

    # Phase 2: receive all verified tokens from pipeline
    # First token arrives after full ring traversal from when it was sent.
    # With interleaving, it was sent at t=0, so arrives at t=first_ring_ms.
    # But phase1 already accounts for that time. After phase1, remaining tokens
    # drain at hop_time intervals.
    #
    # Total tokens in pipeline: draft_tokens + 1 (the original)
    # Tokens already received during phase1: roughly phase1_ms / hop_time
    # Remaining: drain at hop_time each
    #
    # Simplified model matching our benchmarks:
    # recv_all = first_ring_ms + (draft_tokens - 1) * hop_time
    # But with interleaving, phase1 overlaps with early recv.
    # Net recv after phase1 = max(0, first_ring + draft_tokens * hop_time - phase1)
    total_pipeline_ms = first_ring_ms + draft_tokens * hop_time_ms
    recv_after_phase1_ms = max(0, total_pipeline_ms - phase1_ms)

    # Phase 3: resync
    resync_ms = resync_overhead_ms

    cycle_time_ms = phase1_ms + recv_after_phase1_ms + resync_ms

    # Accepted tokens per cycle
    # On average: (draft_tokens + 1) * acceptance_rate tokens accepted
    # (the +1 accounts for the bonus token on rejection)
    accepted_tokens = (draft_tokens + 1) * acceptance_rate

    tok_per_sec = accepted_tokens / (cycle_time_ms / 1000.0)

    return tok_per_sec, cycle_time_ms, {
        "mode": "speculative + interleaved pipeline",
        "hop_time_ms": hop_time_ms,
        "first_ring_ms": first_ring_ms,
        "draft_time_ms": draft_time_ms,
        "phase1_ms": phase1_ms,
        "recv_after_phase1_ms": recv_after_phase1_ms,
        "resync_ms": resync_ms,
        "cycle_time_ms": cycle_time_ms,
        "accepted_tokens": accepted_tokens,
    }


def sweep(args):
    """Sweep phone counts and find optimal configuration."""
    min_phones = calculate_min_phones(
        args.model_size, args.ram, args.draft_size, args.speculative)
    max_phones = args.max_phones

    if min_phones > max_phones:
        print(f"ERROR: Need at least {min_phones} phones but max is {max_phones}.",
              file=sys.stderr)
        sys.exit(1)

    results = []
    for n in range(min_phones, max_phones + 1):
        tok_s, cycle_ms, breakdown = calculate_throughput(
            n_phones=n,
            total_layers=args.layers,
            per_layer_ms=args.per_layer_ms,
            network_overhead_ms=args.network_overhead_ms,
            draft_tokens=args.draft_tokens,
            acceptance_rate=args.acceptance_rate,
            draft_per_token_ms=args.draft_per_token_ms,
            resync_overhead_ms=args.resync_overhead_ms,
            speculative=args.speculative,
            rank0_reduction=args.rank0_reduction,
        )
        layers = calculate_layer_distribution(
            args.layers, n, args.speculative, args.rank0_reduction)
        ram_per_phone = args.model_size / n
        rank0_ram = ram_per_phone + (args.draft_size if args.speculative else 0)

        results.append({
            "n_phones": n,
            "tok_s": tok_s,
            "cycle_ms": cycle_ms,
            "layers": layers,
            "ram_per_phone": ram_per_phone,
            "rank0_ram": rank0_ram,
            "breakdown": breakdown,
        })

    return results


def print_results(results, args):
    """Print sweep results and highlight optimal config."""
    best = max(results, key=lambda r: r["tok_s"])

    print("=" * 78)
    print(f"  Phone Calculator — {args.layers}L model, {args.model_size:.1f} GB, "
          f"{args.ram:.1f} GB RAM/phone")
    if args.speculative:
        print(f"  Speculative: d{args.draft_tokens}, acceptance {args.acceptance_rate:.0%}, "
              f"draft {args.draft_size:.2f} GB")
    else:
        print(f"  Mode: sequential (no speculative decoding)")
    print(f"  Compute: {args.per_layer_ms:.1f} ms/layer, "
          f"network: {args.network_overhead_ms:.1f} ms/hop")
    print("=" * 78)
    print()

    # Summary table
    header = f"{'Phones':>6} │ {'tok/s':>7} │ {'Cycle ms':>9} │ {'RAM/phone':>10} │ {'Rank 0 RAM':>10} │ Layers"
    print(header)
    print("─" * len(header))

    for r in results:
        marker = " ◀ BEST" if r["n_phones"] == best["n_phones"] else ""
        layer_str = ",".join(str(l) for l in r["layers"])
        print(f"{r['n_phones']:>6} │ {r['tok_s']:>7.3f} │ {r['cycle_ms']:>8.0f}ms │ "
              f"{r['ram_per_phone']:>8.2f} GB │ {r['rank0_ram']:>8.2f} GB │ "
              f"[{layer_str}]{marker}")

    print()
    print(f"  Optimal: {best['n_phones']} phones @ {best['tok_s']:.3f} tok/s")
    print()

    # Detailed breakdown for optimal config
    b = best["breakdown"]
    print("─── Timing breakdown (optimal config) ───")
    print(f"  Mode:             {b['mode']}")
    if args.speculative:
        print(f"  Hop time:         {b['hop_time_ms']:.1f} ms")
        print(f"  First ring:       {b['first_ring_ms']:.0f} ms")
        print(f"  Draft time:       {b['draft_time_ms']:.0f} ms")
        print(f"  Phase 1:          {b['phase1_ms']:.0f} ms (draft + send, interleaved)")
        print(f"  Phase 2:          {b['recv_after_phase1_ms']:.0f} ms (pipeline recv)")
        print(f"  Phase 3:          {b['resync_ms']:.0f} ms (resync)")
        print(f"  Total cycle:      {b['cycle_time_ms']:.0f} ms")
        print(f"  Accepted tokens:  {b['accepted_tokens']:.1f} per cycle")
    else:
        print(f"  Ring latency:     {b['ring_latency_ms']:.1f} ms")
        print(f"  Tokens per cycle: {b['tokens_per_cycle']}")
    print()


def print_single(args, n_phones):
    """Print results for a single phone count."""
    tok_s, cycle_ms, breakdown = calculate_throughput(
        n_phones=n_phones,
        total_layers=args.layers,
        per_layer_ms=args.per_layer_ms,
        network_overhead_ms=args.network_overhead_ms,
        draft_tokens=args.draft_tokens,
        acceptance_rate=args.acceptance_rate,
        draft_per_token_ms=args.draft_per_token_ms,
        resync_overhead_ms=args.resync_overhead_ms,
        speculative=args.speculative,
        rank0_reduction=args.rank0_reduction,
    )
    layers = calculate_layer_distribution(
        args.layers, n_phones, args.speculative, args.rank0_reduction)
    ram_per_phone = args.model_size / n_phones
    rank0_ram = ram_per_phone + (args.draft_size if args.speculative else 0)

    print(f"Config: {n_phones} phones, {args.layers} layers, {args.model_size:.1f} GB model")
    print(f"Layers: [{','.join(str(l) for l in layers)}]")
    print(f"RAM:    {ram_per_phone:.2f} GB/phone, rank 0: {rank0_ram:.2f} GB")
    print(f"Est:    {tok_s:.3f} tok/s ({cycle_ms:.0f} ms/cycle)")


def main():
    parser = argparse.ArgumentParser(
        description="Calculate optimal phone count for distributed LLM inference",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # DeepSeek 33B on Z Fold3 (measured: 6.1 tok/s @ 12 phones)
  %(prog)s --layers 62 --model-size 18.6 --ram 5.0

  # Llama 2 7B on Z Fold3
  %(prog)s --layers 32 --model-size 4.0 --ram 5.0

  # 70B model, no speculative decoding
  %(prog)s --layers 80 --model-size 40.0 --ram 5.0 --no-spec

  # Custom SoC (faster, e.g., SD 8 Gen 2)
  %(prog)s --layers 62 --model-size 18.6 --ram 5.0 --per-layer-ms 7

  # Single config (no sweep)
  %(prog)s --layers 62 --model-size 18.6 --ram 5.0 --phones 12
""")

    # Required model parameters
    parser.add_argument("--layers", type=int, required=True,
                        help="Total model layers (e.g., 62 for DeepSeek 33B)")
    parser.add_argument("--model-size", type=float, required=True,
                        help="Model file size in GB (e.g., 18.6 for Q4_K_M)")
    parser.add_argument("--ram", type=float, required=True,
                        help="Available RAM per phone in GB (e.g., 5.0)")

    # Compute parameters
    parser.add_argument("--per-layer-ms", type=float, default=12.0,
                        help="Per-layer compute time in ms (default: 12.0 for Cortex-A78 Q4_K_M)")
    parser.add_argument("--network-overhead-ms", type=float, default=18.0,
                        help="Per-hop network overhead in ms (default: 18.0 for packed ethernet)")

    # Speculative decoding parameters
    parser.add_argument("--no-spec", dest="speculative", action="store_false", default=True,
                        help="Disable speculative decoding")
    parser.add_argument("--draft-size", type=float, default=0.85,
                        help="Draft model size in GB (default: 0.85 for 1.3B Q4_K_M)")
    parser.add_argument("--draft-tokens", type=int, default=24,
                        help="Draft tokens per cycle (default: 24)")
    parser.add_argument("--acceptance-rate", type=float, default=0.745,
                        help="Token acceptance rate (default: 0.745)")
    parser.add_argument("--draft-per-token-ms", type=float, default=38.0,
                        help="Draft model ms per token (default: 38.0)")
    parser.add_argument("--resync-overhead-ms", type=float, default=37.0,
                        help="Resync overhead in ms (default: 37.0)")
    parser.add_argument("--rank0-reduction", type=int, default=6,
                        help="Layers removed from rank 0 for spec overhead (default: 6)")

    # Sweep parameters
    parser.add_argument("--max-phones", type=int, default=20,
                        help="Maximum phones to consider in sweep (default: 20)")
    parser.add_argument("--phones", type=int, default=None,
                        help="Calculate for a specific phone count (skips sweep)")
    parser.add_argument("--sweep", action="store_true", default=False,
                        help="Force sweep output even with --phones")

    # Output format
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")

    args = parser.parse_args()

    if args.phones and not args.sweep:
        # Single config mode
        min_phones = calculate_min_phones(
            args.model_size, args.ram, args.draft_size, args.speculative)
        if args.phones < min_phones:
            print(f"ERROR: {args.phones} phones insufficient. "
                  f"Need at least {min_phones} (RAM constraint).", file=sys.stderr)
            sys.exit(1)
        print_single(args, args.phones)
    else:
        # Sweep mode (default)
        results = sweep(args)

        if args.json:
            import json
            output = {
                "model": {
                    "layers": args.layers,
                    "size_gb": args.model_size,
                    "ram_per_phone_gb": args.ram,
                },
                "speculative": args.speculative,
                "results": [{
                    "n_phones": r["n_phones"],
                    "tok_per_sec": round(r["tok_s"], 3),
                    "cycle_ms": round(r["cycle_ms"], 1),
                    "layers": r["layers"],
                    "ram_per_phone_gb": round(r["ram_per_phone"], 2),
                } for r in results],
                "optimal": {
                    "n_phones": max(results, key=lambda r: r["tok_s"])["n_phones"],
                    "tok_per_sec": round(max(results, key=lambda r: r["tok_s"])["tok_s"], 3),
                },
            }
            print(json.dumps(output, indent=2))
        else:
            print_results(results, args)


if __name__ == "__main__":
    main()
