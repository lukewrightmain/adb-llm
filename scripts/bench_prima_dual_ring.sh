#!/usr/bin/env bash
# Dual-ring benchmark: run two independent 10-phone rings for aggregate throughput.
# Each ring runs its own prima-worker-spec instance independently.
#
# Usage:
#   ./scripts/bench_prima_dual_ring.sh              # 2×10 phones, draft-max=24
#   ./scripts/bench_prima_dual_ring.sh --draft-max 16
#   RING_SIZE=8 ./scripts/bench_prima_dual_ring.sh  # 2×8 phones

set -euo pipefail

DRAFT_MAX=24
N_TOKENS=128
RING_SIZE=${RING_SIZE:-10}
SEED_A=100
SEED_B=200

while [ $# -gt 0 ]; do
    case "$1" in
        --draft-max) shift; DRAFT_MAX="$1" ;;
        -n)          shift; N_TOKENS="$1" ;;
        --seed-a)    shift; SEED_A="$1" ;;
        --seed-b)    shift; SEED_B="$1" ;;
        *)           echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

ADB="$HOME/.local/bin/adb"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

MODEL_QUANT="${MODEL:-Q4_K_M}"
MODEL_REMOTE="/data/local/tmp/adb-llm/models/deepseek-coder-33b-instruct.${MODEL_QUANT}.gguf"
DRAFT_REMOTE="/data/local/tmp/adb-llm/models/deepseek-coder-1.3b-instruct.Q4_K_M.gguf"

# All 20 ethernet phones
ALL_PHONES=(
    10.105.0.12  10.105.0.13  10.105.0.17  10.105.0.19  10.105.0.20
    10.105.0.24  10.105.0.28  10.105.0.29  10.105.0.30  10.105.0.31
    10.105.0.32  10.105.0.36  10.105.0.38  10.105.0.40  10.105.0.41
    10.105.0.42  10.105.0.44  10.105.0.45  10.105.0.48  10.105.0.156
)

TOTAL_LAYERS=62

# Ring A: first RING_SIZE phones, ports 9000/10000
# Ring B: next RING_SIZE phones, ports 11000/12000
RING_A_PHONES=("${ALL_PHONES[@]:0:$RING_SIZE}")
RING_B_PHONES=("${ALL_PHONES[@]:$RING_SIZE:$RING_SIZE}")

RING_A_DATA_PORT=9000
RING_A_SIGNAL_PORT=10000
RING_B_DATA_PORT=11000
RING_B_SIGNAL_PORT=12000

NEEDED=$((RING_SIZE * 2))
if [ "$NEEDED" -gt "${#ALL_PHONES[@]}" ]; then
    echo "ERROR: Need $NEEDED phones for 2×${RING_SIZE} rings, only ${#ALL_PHONES[@]} available"
    exit 1
fi

# =========================================
# Functions
# =========================================

adb_shell() {
    local ip=$1; shift
    $ADB -s "${ip}:5555" shell "$@"
}

cleanup() {
    echo "Cleaning up all phones..."
    for ip in "${ALL_PHONES[@]}"; do
        adb_shell "$ip" "pkill -9 prima-worker; pkill -9 prima-worker-spec" 2>/dev/null || true
    done
    sleep 1
}

calc_layer_weights() {
    local n_phones=$1
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
}

start_ring() {
    local ring_name=$1
    local data_port=$2
    local signal_port=$3
    local lw=$4
    shift 4
    local phones=("$@")
    local n_phones=${#phones[@]}
    local rank0_ip="${phones[0]}"

    echo ""
    echo "--- Starting Ring $ring_name ($n_phones phones, ports $data_port/$signal_port) ---"
    echo "  Phones: ${phones[*]}"
    echo "  Layers: $lw"

    # Start non-rank-0 workers
    for idx in $(seq 1 $((n_phones - 1))); do
        local ip="${phones[$idx]}"
        local rank=$idx
        local next_idx=$(( (rank + 1) % n_phones ))
        local next_ip="${phones[$next_idx]}"

        adb_shell "$ip" "sh -c '
cd /data/local/tmp
taskset f0 ./adb-llm/bin/prima-worker \
  -m $MODEL_REMOTE \
  --world $n_phones --rank $rank \
  --master $rank0_ip --next $next_ip \
  --data-port $data_port --signal-port $signal_port \
  -lw $lw -c 512 -n -1 -t 4 \
  --no-mmap --prefetch \
  > /data/local/tmp/prima-worker.log 2>&1 &
'" 2>/dev/null
        echo "  Started rank $rank on $ip"
    done
}

start_rank0() {
    local ring_name=$1
    local data_port=$2
    local signal_port=$3
    local lw=$4
    local prompt=$5
    local seed=$6
    shift 6
    local phones=("$@")
    local n_phones=${#phones[@]}
    local rank0_ip="${phones[0]}"
    local next_ip="${phones[1]}"

    echo "  Starting rank 0 for Ring $ring_name on $rank0_ip (seed=$seed)..."
    adb_shell "$rank0_ip" "sh -c '
cd /data/local/tmp
taskset f0 ./adb-llm/bin/prima-worker-spec \
  -m $MODEL_REMOTE --model-draft $DRAFT_REMOTE --draft-max $DRAFT_MAX \
  --world $n_phones --rank 0 \
  --master $rank0_ip --next $next_ip \
  --data-port $data_port --signal-port $signal_port \
  -lw $lw -c 512 -t 4 \
  --no-mmap --prefetch \
  -s $seed \
  -p \"$prompt\" \
  -n $N_TOKENS \
  > /data/local/tmp/prima-worker.log 2>&1 &
'" 2>/dev/null
}

wait_for_ring() {
    local ring_name=$1
    local rank0_ip=$2
    local log_file=$3
    local max_wait=600
    local elapsed=0

    while [ "$elapsed" -lt "$max_wait" ]; do
        sleep 5
        elapsed=$((elapsed + 5))
        local pid
        pid=$(adb_shell "$rank0_ip" "pidof prima-worker 2>/dev/null || pidof prima-worker-spec 2>/dev/null" 2>/dev/null | tr -d '\r' || true)
        if [ -z "$pid" ]; then
            echo "  Ring $ring_name finished (${elapsed}s)"
            break
        fi
        local has_perf
        has_perf=$(adb_shell "$rank0_ip" "grep -c llama_perf /data/local/tmp/prima-worker.log 2>/dev/null" | tr -d '\r\n' || echo "0")
        if [ "${has_perf:-0}" -gt 0 ] 2>/dev/null; then
            echo "  Ring $ring_name complete (${elapsed}s)"
            break
        fi
        [ $((elapsed % 30)) -eq 0 ] && echo "  Ring $ring_name still running... (${elapsed}s)"
    done

    adb_shell "$rank0_ip" "cat /data/local/tmp/prima-worker.log" 2>/dev/null > "$log_file"
    echo "  Ring $ring_name log: $log_file"
}

extract_tok_s() {
    local log_file=$1
    grep -oP 'speed:\s+[\d.]+\s+t/s' "$log_file" 2>/dev/null | tail -1 | grep -oP '[\d.]+' || echo "0"
}

# =========================================
# Main
# =========================================

trap cleanup EXIT

LW=$(calc_layer_weights "$RING_SIZE")

echo "============================================"
echo " DUAL RING BENCHMARK"
echo "============================================"
echo "  Ring size: $RING_SIZE phones × 2 rings"
echo "  Model:     $(basename "$MODEL_REMOTE") ($MODEL_QUANT)"
echo "  Draft max: $DRAFT_MAX"
echo "  Tokens:    $N_TOKENS"
echo "  Layers:    $LW"
echo ""

cleanup 2>/dev/null || true
sleep 1

# Verify all phones
echo "Verifying ${NEEDED} phones..."
ok=0
for ip in "${ALL_PHONES[@]:0:$NEEDED}"; do
    if $ADB -s "${ip}:5555" shell "echo ok" 2>/dev/null | grep -q ok; then
        ok=$((ok + 1))
    else
        echo "  $ip: UNREACHABLE!"
    fi
done
echo "  $ok/$NEEDED phones online."
[ "$ok" -eq "$NEEDED" ] || { echo "ERROR: Not all phones reachable!"; exit 1; }

# Start both rings' non-rank-0 workers
start_ring "A" "$RING_A_DATA_PORT" "$RING_A_SIGNAL_PORT" "$LW" "${RING_A_PHONES[@]}"
start_ring "B" "$RING_B_DATA_PORT" "$RING_B_SIGNAL_PORT" "$LW" "${RING_B_PHONES[@]}"

echo ""
echo "Waiting for model load (120s)..."
sleep 120

# Verify non-rank-0 workers (rank 0 uses prima-worker-spec, started later)
all_ok=true
for idx in $(seq 1 $((RING_SIZE - 1))); do
    ip="${RING_A_PHONES[$idx]}"
    pid=$(adb_shell "$ip" "pidof prima-worker" 2>/dev/null | tr -d '\r' || true)
    if [ -z "$pid" ]; then
        echo "  Ring A rank $idx ($ip): NOT RUNNING!"
        adb_shell "$ip" "tail -10 /data/local/tmp/prima-worker.log" 2>/dev/null || true
        all_ok=false
    else
        echo "  Ring A rank $idx ($ip): OK (pid $pid)"
    fi
done
for idx in $(seq 1 $((RING_SIZE - 1))); do
    ip="${RING_B_PHONES[$idx]}"
    pid=$(adb_shell "$ip" "pidof prima-worker" 2>/dev/null | tr -d '\r' || true)
    if [ -z "$pid" ]; then
        echo "  Ring B rank $idx ($ip): NOT RUNNING!"
        adb_shell "$ip" "tail -10 /data/local/tmp/prima-worker.log" 2>/dev/null || true
        all_ok=false
    else
        echo "  Ring B rank $idx ($ip): OK (pid $pid)"
    fi
done
$all_ok || { echo "ERROR: Some workers failed!"; exit 1; }
echo "All non-rank-0 workers ready."

# Start rank 0 for both rings simultaneously
PROMPT_A="Write a Python function that computes the Fibonacci sequence efficiently using dynamic programming."
PROMPT_B="Write a Python function that implements a binary search tree with insert, delete, and search operations."

start_rank0 "A" "$RING_A_DATA_PORT" "$RING_A_SIGNAL_PORT" "$LW" "$PROMPT_A" "$SEED_A" "${RING_A_PHONES[@]}"
start_rank0 "B" "$RING_B_DATA_PORT" "$RING_B_SIGNAL_PORT" "$LW" "$PROMPT_B" "$SEED_B" "${RING_B_PHONES[@]}"

echo ""
echo "Both rings running. Waiting for completion..."

LOG_A="/tmp/prima-dual-ringA-${RING_SIZE}phone-spec-d${DRAFT_MAX}-${MODEL_QUANT}.log"
LOG_B="/tmp/prima-dual-ringB-${RING_SIZE}phone-spec-d${DRAFT_MAX}-${MODEL_QUANT}.log"

# Wait for both (poll in parallel)
max_wait=600
elapsed=0
a_done=false
b_done=false

while [ "$elapsed" -lt "$max_wait" ]; do
    sleep 5
    elapsed=$((elapsed + 5))

    if ! $a_done; then
        pid_a=$(adb_shell "${RING_A_PHONES[0]}" "pidof prima-worker-spec 2>/dev/null" 2>/dev/null | tr -d '\r' || true)
        [ -z "$pid_a" ] && a_done=true && echo "  Ring A finished (${elapsed}s)"
    fi
    if ! $b_done; then
        pid_b=$(adb_shell "${RING_B_PHONES[0]}" "pidof prima-worker-spec 2>/dev/null" 2>/dev/null | tr -d '\r' || true)
        [ -z "$pid_b" ] && b_done=true && echo "  Ring B finished (${elapsed}s)"
    fi

    $a_done && $b_done && break
    [ $((elapsed % 30)) -eq 0 ] && echo "  Still running... (${elapsed}s)"
done

# Fetch logs
adb_shell "${RING_A_PHONES[0]}" "cat /data/local/tmp/prima-worker.log" 2>/dev/null > "$LOG_A"
adb_shell "${RING_B_PHONES[0]}" "cat /data/local/tmp/prima-worker.log" 2>/dev/null > "$LOG_B"

# Extract results
TOK_A=$(extract_tok_s "$LOG_A")
TOK_B=$(extract_tok_s "$LOG_B")

echo ""
echo "============================================"
echo " DUAL RING RESULTS"
echo "============================================"
echo "  Ring A (${RING_A_PHONES[0]}): ${TOK_A} tok/s"
echo "  Ring B (${RING_B_PHONES[0]}): ${TOK_B} tok/s"

# Calculate aggregate (use awk for float addition)
TOTAL=$(echo "$TOK_A $TOK_B" | awk '{printf "%.3f", $1 + $2}')
echo "  AGGREGATE: ${TOTAL} tok/s"
echo ""
echo "  Ring A log: $LOG_A"
echo "  Ring B log: $LOG_B"
echo "============================================"

# Print key metrics from both logs
echo ""
echo "--- Ring A Metrics ---"
grep -E "(speed:|n_draft|n_predict|n_drafted|n_accept|accept)" "$LOG_A" 2>/dev/null || echo "  No metrics"
echo ""
echo "--- Ring B Metrics ---"
grep -E "(speed:|n_draft|n_predict|n_drafted|n_accept|accept)" "$LOG_B" 2>/dev/null || echo "  No metrics"

cleanup 2>/dev/null || true
