#!/usr/bin/env bash
# Benchmark prima.cpp phone-only ring on ETHERNET phones — direct IP communication.
# No tunnels, no WinPC relay, no SSH. Phones talk directly at 10.105.0.x.
#
# Usage:
#   ./scripts/bench_prima_ethernet.sh 4                # 4-phone ring, Q4_K_M
#   ./scripts/bench_prima_ethernet.sh 5                # 5-phone ring
#   ./scripts/bench_prima_ethernet.sh 10               # 10-phone ring
#   ./scripts/bench_prima_ethernet.sh 20               # all 20 phones
#   ./scripts/bench_prima_ethernet.sh 5 --spec         # speculative decoding (draft-max=24)
#   ./scripts/bench_prima_ethernet.sh 5 --spec --draft-max 32  # custom draft-max
#   ./scripts/bench_prima_ethernet.sh 5 --sweep        # sweep 4,5,8,10,15,20
#   ./scripts/bench_prima_ethernet.sh 5 --spec-sweep   # sweep spec with draft-max 8,16,24,32
#   MODEL=Q4_0 ./scripts/bench_prima_ethernet.sh 5     # use Q4_0 model
#
# Prerequisites:
#   - adb at ~/.local/bin/adb, connected to ethernet phones
#   - prima-worker + model deployed to phones

set -euo pipefail

N_PHONES=${1:-5}
SPEC_MODE=false
SWEEP_MODE=false
SPEC_SWEEP_MODE=false
DRAFT_MAX=24
N_TOKENS=128
SEED=42
NO_INTERLEAVE=false
BATCH_PIPELINE="${PRIMA_BATCH_PIPELINE:-1}"
FLASH_ATTN=false
CTX_SIZE=512
THREADS=4
TASKSET="f0"
EXTRA_FLAGS=""

shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        --spec)           SPEC_MODE=true ;;
        --sweep)          SWEEP_MODE=true ;;
        --spec-sweep)     SPEC_SWEEP_MODE=true; SPEC_MODE=true ;;
        --draft-max)      shift; DRAFT_MAX="$1" ;;
        -n)               shift; N_TOKENS="$1" ;;
        --seed)           shift; SEED="$1" ;;
        --no-interleave)  NO_INTERLEAVE=true ;;
        --batch-pipeline) shift; BATCH_PIPELINE="$1" ;;
        -fa|--flash-attn) FLASH_ATTN=true ;;
        -c)               shift; CTX_SIZE="$1" ;;
        -t)               shift; THREADS="$1" ;;
        --taskset)        shift; TASKSET="$1" ;;
        --extra)          shift; EXTRA_FLAGS="$1" ;;
        *)                echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

ADB="$HOME/.local/bin/adb"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

MODEL_QUANT="${MODEL:-Q4_K_M}"
MODEL_REMOTE="/data/local/tmp/adb-llm/models/deepseek-coder-33b-instruct.${MODEL_QUANT}.gguf"
DRAFT_REMOTE="/data/local/tmp/adb-llm/models/deepseek-coder-1.3b-instruct.Q4_K_M.gguf"

# All 20 ethernet phones (ordered by IP)
ALL_PHONES=(
    10.105.0.12
    10.105.0.13
    10.105.0.17
    10.105.0.19
    10.105.0.20
    10.105.0.24
    10.105.0.28
    10.105.0.29
    10.105.0.30
    10.105.0.31
    10.105.0.32
    10.105.0.36
    10.105.0.38
    10.105.0.40
    10.105.0.41
    10.105.0.42
    10.105.0.44
    10.105.0.45
    10.105.0.48
    10.105.0.156
)

TOTAL_LAYERS=62  # DeepSeek Coder 33B
DATA_PORT=9000
SIGNAL_PORT=10000

# =========================================
# Functions
# =========================================

adb_shell() {
    local ip=$1
    shift
    $ADB -s "${ip}:5555" shell "$@"
}

cleanup() {
    echo "Cleaning up..."
    for ip in "${ALL_PHONES[@]}"; do
        adb_shell "$ip" "pkill -9 prima-worker; pkill -9 prima-worker-spec" 2>/dev/null || true
    done
    sleep 1
    echo "Cleanup done."
}

calc_layer_weights() {
    local n_phones=$1
    local spec=${2:-false}

    if [ "$spec" = "true" ]; then
        local rank0_reduction=6
        local rank0_layers=$(( (TOTAL_LAYERS / n_phones) - rank0_reduction ))
        [ "$rank0_layers" -lt 5 ] && rank0_layers=5
        local remaining=$((TOTAL_LAYERS - rank0_layers))
        local other_phones=$((n_phones - 1))
        local per_other=$((remaining / other_phones))
        local other_remainder=$((remaining % other_phones))
        local lw="$rank0_layers"
        for i in $(seq 1 $other_phones); do
            local extra=0
            [ "$((i - 1))" -lt "$other_remainder" ] && extra=1
            lw="${lw},$((per_other + extra))"
        done
        echo "$lw"
    else
        local layers_per_phone=$((TOTAL_LAYERS / n_phones))
        local remainder=$((TOTAL_LAYERS % n_phones))
        local lw=""
        for i in $(seq 0 $((n_phones - 1))); do
            local extra=0
            [ "$i" -lt "$remainder" ] && extra=1
            [ -n "$lw" ] && lw="${lw},$((layers_per_phone + extra))" || lw="$((layers_per_phone + extra))"
        done
        echo "$lw"
    fi
}

verify_phones() {
    local n_phones=$1
    local phones=("${@:2}")

    echo "Verifying $n_phones ethernet phones..."
    local ok=0
    for ip in "${phones[@]}"; do
        if $ADB -s "${ip}:5555" shell "echo ok" 2>/dev/null | grep -q ok; then
            echo "  $ip: reachable"
            ok=$((ok + 1))
        else
            echo "  $ip: UNREACHABLE!"
        fi
    done
    echo "  $ok/$n_phones phones online."
    [ "$ok" -eq "$n_phones" ] || { echo "ERROR: Not all phones reachable!"; exit 1; }
}

verify_phone_connectivity() {
    local n_phones=$1
    local phones=("${@:2}")

    echo "Testing phone-to-phone connectivity (ring path)..."
    for idx in $(seq 0 $((n_phones - 1))); do
        local src="${phones[$idx]}"
        local next_idx=$(( (idx + 1) % n_phones ))
        local dst="${phones[$next_idx]}"
        local result
        result=$(adb_shell "$src" "ping -c 1 -W 2 $dst 2>/dev/null" | grep "time=" | awk -F'time=' '{print $2}' || echo "FAIL")
        echo "  rank $idx ($src) → rank $next_idx ($dst): $result"
    done
}

start_phone_workers() {
    local n_phones=$1
    local lw=$2
    local spec=$3
    local phones=("${@:4}")
    local rank0_ip="${phones[0]}"

    echo "Starting prima-workers on $n_phones phones (direct IP)..."

    # Start non-rank-0 workers first (they wait for rank 0)
    for idx in $(seq 1 $((n_phones - 1))); do
        local ip="${phones[$idx]}"
        local rank=$idx
        local next_idx=$(( (rank + 1) % n_phones ))
        local next_ip="${phones[$next_idx]}"

        local fa_flag=""
        [ "$FLASH_ATTN" = "true" ] && fa_flag="-fa"

        adb_shell "$ip" "sh -c '
cd /data/local/tmp
taskset $TASKSET ./adb-llm/bin/prima-worker \
  -m $MODEL_REMOTE \
  --world $n_phones --rank $rank \
  --master $rank0_ip --next $next_ip \
  --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
  -lw $lw -c $CTX_SIZE -n -1 -t $THREADS -tb $THREADS \
  --no-mmap --prefetch $fa_flag $EXTRA_FLAGS \
  > /data/local/tmp/prima-worker.log 2>&1 &
'" 2>/dev/null
        echo "  Started rank $rank on $ip (next=$next_ip, master=$rank0_ip)"
    done

    echo "Waiting for non-rank-0 workers to load model (120s for --no-mmap)..."
    sleep 120

    # Verify non-rank-0 workers
    local all_ok=true
    for idx in $(seq 1 $((n_phones - 1))); do
        local ip="${phones[$idx]}"
        local pid
        pid=$(adb_shell "$ip" "pidof prima-worker" 2>/dev/null | tr -d '\r' || true)
        if [ -n "$pid" ]; then
            echo "  $ip (rank $idx): running (pid $pid)"
        else
            echo "  $ip (rank $idx): NOT RUNNING!"
            adb_shell "$ip" "tail -20 /data/local/tmp/prima-worker.log" 2>/dev/null || true
            all_ok=false
        fi
    done

    if ! $all_ok; then
        echo "ERROR: Some workers failed to start!"
        return 1
    fi

    # Start rank 0 (drives generation)
    local next_ip="${phones[1]}"
    local binary="prima-worker"
    local extra_flags=""
    if [ "$spec" = "true" ]; then
        binary="prima-worker-spec"
        extra_flags="--model-draft $DRAFT_REMOTE --draft-max $DRAFT_MAX"
    fi

    local env_prefix="PRIMA_BATCH_PIPELINE=$BATCH_PIPELINE "
    if [ "$NO_INTERLEAVE" = "true" ]; then
        env_prefix="${env_prefix}PRIMA_NO_INTERLEAVE=1 "
        echo "  (interleaving DISABLED)"
    fi

    local fa_flag=""
    [ "$FLASH_ATTN" = "true" ] && fa_flag="-fa"

    echo ""
    echo "Starting rank 0 on $rank0_ip (next=$next_ip, batch_pipeline=$BATCH_PIPELINE, ctx=$CTX_SIZE, threads=$THREADS, fa=$FLASH_ATTN)..."
    adb_shell "$rank0_ip" "sh -c '
cd /data/local/tmp
${env_prefix}taskset $TASKSET ./adb-llm/bin/$binary \
  -m $MODEL_REMOTE $extra_flags \
  --world $n_phones --rank 0 \
  --master $rank0_ip --next $next_ip \
  --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
  -lw $lw -c $CTX_SIZE -t $THREADS -tb $THREADS \
  --no-mmap --prefetch $fa_flag $EXTRA_FLAGS \
  -s $SEED \
  -p \"Write a Python function that computes the Fibonacci sequence efficiently using dynamic programming.\" \
  -n $N_TOKENS \
  > /data/local/tmp/prima-worker.log 2>&1 &
'" 2>/dev/null
    echo "  Rank 0 started. Waiting for inference..."
}

wait_and_report() {
    local rank0_ip=$1
    local n_phones=$2
    local lw=$3
    local log_file=$4
    local phones=("${@:5}")

    echo ""
    echo "Monitoring rank 0 output..."

    local max_wait=600
    local elapsed=0
    local done=false

    while [ "$elapsed" -lt "$max_wait" ]; do
        sleep 5
        elapsed=$((elapsed + 5))

        local pid
        pid=$(adb_shell "$rank0_ip" "pidof prima-worker 2>/dev/null || pidof prima-worker-spec 2>/dev/null" 2>/dev/null | tr -d '\r' || true)

        if [ -z "$pid" ]; then
            echo "  Rank 0 finished (${elapsed}s elapsed)."
            done=true
            break
        fi

        local has_perf
        has_perf=$(adb_shell "$rank0_ip" "grep -c llama_perf /data/local/tmp/prima-worker.log 2>/dev/null" | tr -d '\r\n' || echo "0")
        if [ "${has_perf:-0}" -gt 0 ] 2>/dev/null; then
            echo "  Inference complete (${elapsed}s elapsed)."
            done=true
            break
        fi

        echo "  Still running... (${elapsed}s)"
    done

    if ! $done; then
        echo "  TIMEOUT after ${max_wait}s!"
    fi

    # Fetch rank 0 log
    echo ""
    echo "--- Rank 0 Log ($rank0_ip) ---"
    adb_shell "$rank0_ip" "cat /data/local/tmp/prima-worker.log" 2>/dev/null | tee "$log_file"

    # Fetch other rank logs (last 30 lines)
    for idx in $(seq 1 $((n_phones - 1))); do
        local ip="${phones[$idx]}"
        echo ""
        echo "--- Rank $idx Log ($ip) [last 30 lines] ---"
        adb_shell "$ip" "tail -30 /data/local/tmp/prima-worker.log" 2>/dev/null || echo "  (no log)"
    done

    echo ""
    echo "--- Key Metrics ---"
    grep -E "(speed:|tok/s|n_draft|n_predict|n_drafted|n_accept|accept|eval time|sample time|total time)" "$log_file" 2>/dev/null || echo "  No metrics found."

    echo ""
    echo "Log saved: $log_file"
}

run_benchmark() {
    local n_phones=$1
    local spec=$2
    local phones=("${ALL_PHONES[@]:0:$n_phones}")
    local lw
    lw=$(calc_layer_weights "$n_phones" "$spec")

    local mode_str="ethernet direct-IP"
    [ "$spec" = "true" ] && mode_str="$mode_str + speculative (draft-max=$DRAFT_MAX)"

    echo ""
    echo "============================================"
    echo " ${n_phones}-PHONE ETHERNET RING — $mode_str"
    echo "============================================"
    echo "  Model:     $(basename "$MODEL_REMOTE") ($MODEL_QUANT)"
    echo "  Phones:    ${phones[*]}"
    echo "  Layers:    $lw"
    echo "  Transport: Direct ethernet (NO tunnels)"
    if [ "$spec" = "true" ]; then
        echo "  Draft:     $(basename "$DRAFT_REMOTE")"
        echo "  Draft max: $DRAFT_MAX tokens"
        echo "  Batch pipeline: $BATCH_PIPELINE"
    fi
    echo "  Context:   $CTX_SIZE"
    echo "  Threads:   $THREADS"
    echo "  Flash attn: $FLASH_ATTN"
    [ -n "$EXTRA_FLAGS" ] && echo "  Extra:     $EXTRA_FLAGS"
    echo ""

    cleanup 2>/dev/null || true
    sleep 1

    verify_phones "$n_phones" "${phones[@]}"
    verify_phone_connectivity "$n_phones" "${phones[@]}"

    local log_file="/tmp/prima-ethernet-${n_phones}phone"
    [ "$spec" = "true" ] && log_file="${log_file}-spec-d${DRAFT_MAX}"
    log_file="${log_file}-${MODEL_QUANT}.log"

    start_phone_workers "$n_phones" "$lw" "$spec" "${phones[@]}"
    wait_and_report "${phones[0]}" "$n_phones" "$lw" "$log_file" "${phones[@]}"

    cleanup 2>/dev/null || true
}

# =========================================
# Main
# =========================================

trap cleanup EXIT

if $SPEC_SWEEP_MODE; then
    echo "============================================"
    echo " SPECULATIVE SWEEP — ${N_PHONES} phones, draft-max: 8, 16, 24, 32"
    echo "============================================"
    echo ""
    for dm in 8 16 24 32; do
        DRAFT_MAX=$dm
        run_benchmark "$N_PHONES" "true"
        echo ""
        sleep 5
    done
    echo ""
    echo "============================================"
    echo " SPEC SWEEP COMPLETE — check logs:"
    for dm in 8 16 24 32; do
        echo "  /tmp/prima-ethernet-${N_PHONES}phone-spec-d${dm}-${MODEL_QUANT}.log"
    done
    echo "============================================"
elif $SWEEP_MODE; then
    echo "============================================"
    echo " ETHERNET SWEEP — testing 4, 5, 8, 10, 15, 20 phones"
    echo "============================================"
    echo ""
    for n in 4 5 8 10 15 20; do
        [ "$n" -le "${#ALL_PHONES[@]}" ] || continue
        run_benchmark "$n" "$SPEC_MODE"
        echo ""
        sleep 5
    done
    echo ""
    echo "============================================"
    echo " SWEEP COMPLETE — check logs:"
    for n in 4 5 8 10 15 20; do
        [ "$n" -le "${#ALL_PHONES[@]}" ] || continue
        suffix="${MODEL_QUANT}"
        $SPEC_MODE && suffix="spec-d${DRAFT_MAX}-${MODEL_QUANT}"
        echo "  /tmp/prima-ethernet-${n}phone-${suffix}.log"
    done
    echo "============================================"
else
    run_benchmark "$N_PHONES" "$SPEC_MODE"
fi
