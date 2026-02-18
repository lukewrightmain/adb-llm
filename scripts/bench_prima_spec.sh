#!/usr/bin/env bash
# Benchmark prima.cpp with speculative decoding (draft model on host).
#
# Usage:
#   ./scripts/bench_prima_spec.sh 2                    # 2-node, default (draft-max=8)
#   ./scripts/bench_prima_spec.sh 2 8 42               # 2-node, draft-max=8, host=42 layers
#   ./scripts/bench_prima_spec.sh 4 16 20              # 4-node, draft-max=16, host=20 layers
#   DRAFT_MODEL=~/models/other.gguf ./scripts/bench_prima_spec.sh 2
#
# Prerequisites:
#   - bin/prima-host-spec built (./scripts/build_prima.sh)
#   - Draft model downloaded (deepseek-coder-1.3b-instruct.Q4_K_M.gguf)
#   - Phones have prima-worker + target model deployed

set -euo pipefail

WORLD_SIZE=${1:-2}
DRAFT_MAX=${2:-8}
HOST_LAYERS=${3:-42}
N_PHONES=$((WORLD_SIZE - 1))

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PRIMA_HOST_SPEC="$PROJECT_DIR/bin/prima-host-spec"
ADB='"C:\Program Files\platform-tools\adb.exe"'
SSH_HOST="winpc"

# Models
MODEL_TARGET="$HOME/models/deepseek-coder-33b-instruct.Q4_K_M.gguf"
DRAFT_MODEL="${DRAFT_MODEL:-$HOME/models/deepseek-coder-1.3b-instruct.Q4_K_M.gguf}"
MODEL_REMOTE="/data/local/tmp/adb-llm/models/deepseek-coder-33b-instruct.Q4_K_M.gguf"

# Phones
ALL_PHONES=(R3CR904AQKA R3CR90AJCPF R3CRA0KPK9M R3CRB0726FZ
            R3CRA0CJCDD R3CRA0CP7TZ R3CRA0F51KB R3CRA0E77WZ
            R3CR904BTTM R3CRA0CZ35E R3CRA0D4LJZ R3CRA0D7E6Z
            R3CRA0D98AZ R3CRA0DQKVZ R3CRA0EK7HM R3CRA0FR3VD
            R3CRA0GJ4SF R3CRA0GJK0E R3CRA0GJK5M R3CRA0GK04F)
PHONES=("${ALL_PHONES[@]:0:$N_PHONES}")

TOTAL_LAYERS=62  # DeepSeek Coder 33B

# Ports
DATA_PORT=9000
SIGNAL_PORT=10000
FWD_BASE=59001
REV_BASE=58001
SIG_OFFSET=100

# Validate
if [ ! -f "$PRIMA_HOST_SPEC" ]; then
    echo "ERROR: $PRIMA_HOST_SPEC not found. Run ./scripts/build_prima.sh first."
    exit 1
fi
if [ ! -f "$DRAFT_MODEL" ]; then
    echo "ERROR: Draft model not found at $DRAFT_MODEL"
    echo "Download with: huggingface-cli download TheBloke/deepseek-coder-1.3b-instruct-GGUF deepseek-coder-1.3b-instruct.Q4_K_M.gguf --local-dir ~/models/"
    exit 1
fi
if [ ! -f "$MODEL_TARGET" ]; then
    echo "ERROR: Target model not found at $MODEL_TARGET"
    exit 1
fi

# Calculate layer distribution
PHONE_LAYERS=$((TOTAL_LAYERS - HOST_LAYERS))
if [ "$N_PHONES" -gt 0 ]; then
    LAYERS_PER_PHONE=$((PHONE_LAYERS / N_PHONES))
    REMAINDER=$((PHONE_LAYERS % N_PHONES))
else
    LAYERS_PER_PHONE=0
    REMAINDER=0
fi
LW="$HOST_LAYERS"
for i in $(seq 0 $((N_PHONES - 1))); do
    EXTRA=0
    if [ "$i" -lt "$REMAINDER" ]; then
        EXTRA=1
    fi
    LW="${LW},$((LAYERS_PER_PHONE + EXTRA))"
done

echo "============================================"
echo " prima.cpp SPECULATIVE benchmark — ${WORLD_SIZE}-node ring"
echo "============================================"
echo "  Phones:       ${PHONES[*]:-none}"
echo "  Layers:       $LW (host=$HOST_LAYERS, phone=$PHONE_LAYERS)"
echo "  Draft model:  $(basename "$DRAFT_MODEL")"
echo "  Draft max:    $DRAFT_MAX tokens"
echo ""

cleanup() {
    echo "Cleaning up..."
    for SERIAL in "${ALL_PHONES[@]}"; do
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell pkill -f prima-worker" 2>/dev/null || true
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL forward --remove-all" 2>/dev/null || true
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL reverse --remove-all" 2>/dev/null || true
    done
    for port in $(seq $DATA_PORT $((DATA_PORT + 20))) $(seq $SIGNAL_PORT $((SIGNAL_PORT + 20))); do
        fuser -k "${port}/tcp" 2>/dev/null || true
    done
    pkill -f prima-host-spec 2>/dev/null || true
    pkill -f prima-host 2>/dev/null || true
    rm -f /tmp/ssh-prima-* 2>/dev/null
    echo "Cleanup done."
}

trap cleanup EXIT
cleanup 2>/dev/null || true
sleep 1

# --- Set up tunnels (same as bench_prima.sh) ---
echo "Setting up tunnels..."

for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    RANK=$((idx + 1))

    PHONE_DATA_BIND=$((DATA_PORT + RANK))
    PHONE_SIGNAL_BIND=$((SIGNAL_PORT + RANK))

    WIN_FWD_DATA=$((FWD_BASE + idx))
    WIN_FWD_SIG=$((FWD_BASE + SIG_OFFSET + idx))

    echo "  [$SERIAL] ADB forward: winpc:$WIN_FWD_DATA -> phone:$PHONE_DATA_BIND"
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL forward tcp:$WIN_FWD_DATA tcp:$PHONE_DATA_BIND"
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL forward tcp:$WIN_FWD_SIG tcp:$PHONE_SIGNAL_BIND"

    ssh -o ConnectTimeout=10 -o Compression=no -N \
        -L "$PHONE_DATA_BIND:127.0.0.1:$WIN_FWD_DATA" \
        -L "$PHONE_SIGNAL_BIND:127.0.0.1:$WIN_FWD_SIG" \
        "$SSH_HOST" -o ServerAliveInterval=10 &
    echo "  [$SERIAL] SSH -L: host:$PHONE_DATA_BIND -> winpc:$WIN_FWD_DATA -> phone:$PHONE_DATA_BIND"
done

sleep 1

# Outbound: phone -> successor
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    RANK=$((idx + 1))
    NEXT_RANK=$(( (RANK + 1) % WORLD_SIZE ))

    PHONE_CONNECT_DATA=$((DATA_PORT + NEXT_RANK))
    PHONE_CONNECT_SIG=$((SIGNAL_PORT + NEXT_RANK))

    if [ "$NEXT_RANK" -eq 0 ]; then
        WIN_REV_DATA=$((REV_BASE + idx))
        WIN_REV_SIG=$((REV_BASE + SIG_OFFSET + idx))

        ssh -o ConnectTimeout=10 -o Compression=no -N \
            -R "$WIN_REV_DATA:127.0.0.1:$DATA_PORT" \
            -R "$WIN_REV_SIG:127.0.0.1:$SIGNAL_PORT" \
            "$SSH_HOST" -o ServerAliveInterval=10 &
        sleep 0.5

        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL reverse tcp:$PHONE_CONNECT_DATA tcp:$WIN_REV_DATA"
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL reverse tcp:$PHONE_CONNECT_SIG tcp:$WIN_REV_SIG"
        echo "  [$SERIAL] Outbound (-> HOST): phone:$PHONE_CONNECT_DATA -> winpc:$WIN_REV_DATA -> SSH -> host:$DATA_PORT"
    else
        NEXT_IDX=$((NEXT_RANK - 1))
        NEXT_FWD_DATA=$((FWD_BASE + NEXT_IDX))
        NEXT_FWD_SIG=$((FWD_BASE + SIG_OFFSET + NEXT_IDX))

        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL reverse tcp:$PHONE_CONNECT_DATA tcp:$NEXT_FWD_DATA"
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL reverse tcp:$PHONE_CONNECT_SIG tcp:$NEXT_FWD_SIG"
        echo "  [$SERIAL] DIRECT (-> Phone): phone:$PHONE_CONNECT_DATA -> winpc:$NEXT_FWD_DATA -> next phone"
    fi
done

echo ""
echo "Tunnels established. Waiting for connections to settle..."
sleep 2

# --- Start prima-workers on phones ---
echo ""
echo "Starting prima-workers..."
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    RANK=$((idx + 1))

    SCRIPT_LOCAL="/tmp/prima-start-rank${RANK}.sh"

    cat > "$SCRIPT_LOCAL" << ENDSCRIPT
#!/system/bin/sh
cd /data/local/tmp
taskset f0 ./adb-llm/bin/prima-worker \
  -m $MODEL_REMOTE \
  --world $WORLD_SIZE --rank $RANK \
  --master 127.0.0.1 --next 127.0.0.1 \
  --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
  -lw $LW -c 512 -n -1 \
  --prefetch \
  > /data/local/tmp/prima-worker.log 2>&1 &
ENDSCRIPT

    SCRIPT_WIN="C:\\Users\\Lukio-4090\\prima-start-rank${RANK}.sh"
    SCRIPT_PHONE="/data/local/tmp/prima-start.sh"

    scp -o ConnectTimeout=10 "$SCRIPT_LOCAL" "${SSH_HOST}:${SCRIPT_WIN}" >/dev/null 2>&1
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL push \"$SCRIPT_WIN\" $SCRIPT_PHONE" >/dev/null 2>&1
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell chmod 755 $SCRIPT_PHONE" 2>/dev/null

    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell sh $SCRIPT_PHONE" 2>/dev/null
    echo "  Started rank $RANK on $SERIAL"
done

echo "Waiting for workers to initialize (30s)..."
sleep 30

# Verify workers
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    PID=$(ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell pidof prima-worker" 2>/dev/null || true)
    if [ -n "$PID" ]; then
        echo "  $SERIAL: prima-worker running (pid $PID)"
    else
        echo "  $SERIAL: WARNING — prima-worker NOT running!"
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell tail -20 /data/local/tmp/prima-worker.log" 2>/dev/null || true
    fi
done

# --- Run speculative benchmark ---
LOG_FILE="/tmp/prima-spec-${WORLD_SIZE}node-d${DRAFT_MAX}-h${HOST_LAYERS}.log"

echo ""
echo "============================================"
echo " SPECULATIVE DECODING BENCHMARK"
echo " ${WORLD_SIZE}-node, layers=$LW, draft-max=$DRAFT_MAX"
echo "============================================"

time "$PRIMA_HOST_SPEC" \
    -m "$MODEL_TARGET" \
    --model-draft "$DRAFT_MODEL" \
    --world "$WORLD_SIZE" --rank 0 \
    --next 127.0.0.1 --master 127.0.0.1 \
    --data-port "$DATA_PORT" --signal-port "$SIGNAL_PORT" \
    -lw "$LW" -c 512 -t 48 \
    --prefetch \
    --draft-max "$DRAFT_MAX" \
    -p "Write a Python function that computes the Fibonacci sequence efficiently using dynamic programming." \
    -n 128 2>&1 | tee "$LOG_FILE"

echo ""
echo "============================================"
echo " Benchmark complete. Log: $LOG_FILE"
echo "============================================"

# Extract key metrics from log
echo ""
echo "--- Key Metrics ---"
grep -E "(speed:|n_draft|n_predict|n_drafted|n_accept|accept)" "$LOG_FILE" 2>/dev/null || true
