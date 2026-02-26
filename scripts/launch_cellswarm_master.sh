#!/usr/bin/env bash
# Launch cellswarm-master on phone ring — self-contained HTTP+inference server.
#
# Deploys cellswarm-master to rank 0, cellswarm-worker to rank 1-N,
# starts the ring, and prints the dashboard URL.
#
# Usage:
#   ./scripts/launch_cellswarm_master.sh 12                   # 12-phone ring
#   ./scripts/launch_cellswarm_master.sh 12 --draft-max 32    # custom draft-max
#   ./scripts/launch_cellswarm_master.sh 12 --port 9090       # custom HTTP port
#   ./scripts/launch_cellswarm_master.sh 12 --deploy          # deploy binaries first
#   ./scripts/launch_cellswarm_master.sh 12 --ctx 2048        # context size
#
# Prerequisites:
#   - cellswarm-master + cellswarm-worker binaries built (run build_cellswarm.sh)
#   - Models deployed to phones (run deploy_model.sh)
#   - Ethernet phones reachable via adb

set -euo pipefail

N_PHONES=${1:-12}
DRAFT_MAX=24
HTTP_PORT=8080
CTX_SIZE=2048
THREADS=4
TASKSET="f0"
DEPLOY=false
OVERLAP_DRAFT=0
EXTRA_FLAGS=""

shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        --draft-max)  shift; DRAFT_MAX="$1" ;;
        --port)       shift; HTTP_PORT="$1" ;;
        --ctx|-c)     shift; CTX_SIZE="$1" ;;
        -t)           shift; THREADS="$1" ;;
        --taskset)    shift; TASKSET="$1" ;;
        --deploy)     DEPLOY=true ;;
        --overlap)    OVERLAP_DRAFT=1 ;;
        --extra)      shift; EXTRA_FLAGS="$1" ;;
        *)            echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

ADB="$HOME/.local/bin/adb"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="$PROJECT_DIR/bin"

MODEL_QUANT="${MODEL_QUANT:-Q4_K_M}"

# Model family: "deepseek" (default) or "qwen2.5"
MODEL_FAMILY="${MODEL_FAMILY:-deepseek}"

if [ "$MODEL_FAMILY" = "qwen2.5" ] || [ "$MODEL_FAMILY" = "qwen" ]; then
    MODEL_REMOTE="/data/local/tmp/cellswarm/models/Qwen2.5-Coder-32B-Instruct-Q4_K_M.gguf"
    DRAFT_REMOTE="/data/local/tmp/cellswarm/models/Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf"
    TOTAL_LAYERS=64
elif [ "$MODEL_FAMILY" = "qwen3.5-moe" ]; then
    # Qwen3.5-35B-A3B: MoE — 35B total, 3B active, 256 experts, 40 layers
    MODEL_REMOTE="/data/local/tmp/cellswarm/models/Qwen3.5-35B-A3B-Q4_K_M.gguf"
    DRAFT_REMOTE="/data/local/tmp/cellswarm/models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
    TOTAL_LAYERS=40
else
    MODEL_REMOTE="/data/local/tmp/cellswarm/models/deepseek-coder-33b-instruct.${MODEL_QUANT}.gguf"
    DRAFT_REMOTE="/data/local/tmp/cellswarm/models/deepseek-coder-1.3b-instruct.Q4_K_M.gguf"
    TOTAL_LAYERS=62
fi

# Allow full override via env
MODEL_REMOTE="${MODEL_PATH:-$MODEL_REMOTE}"
DRAFT_REMOTE="${DRAFT_PATH:-$DRAFT_REMOTE}"
TOTAL_LAYERS="${TOTAL_LAYERS_OVERRIDE:-$TOTAL_LAYERS}"
DATA_PORT=9000
SIGNAL_PORT=10000

# All 20 ethernet phones (ordered: model-ready first, then others)
ALL_PHONES=(
    10.105.0.41
    10.105.0.45
    10.105.0.48
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
    10.105.0.156
    10.105.0.36
    10.105.0.38
    10.105.0.40
    10.105.0.42
    10.105.0.44
)

PHONES=("${ALL_PHONES[@]:0:$N_PHONES}")

adb_shell() {
    local ip=$1; shift
    $ADB -s "${ip}:5555" shell "$@"
}

cleanup() {
    echo "Cleaning up all phones..."
    for ip in "${PHONES[@]}"; do
        adb_shell "$ip" "pkill -9 cellswarm-worker; pkill -9 cellswarm-worker-spec; pkill -9 cellswarm-master; pkill -9 mdns-advertise" 2>/dev/null || true
    done
    sleep 1
}

calc_layer_weights() {
    local n=$1
    # Rank 0 (master) gets fewer layers — it also runs HTTP + draft model
    local rank0_reduction=6
    local rank0_layers=$(( (TOTAL_LAYERS / n) - rank0_reduction ))
    [ "$rank0_layers" -lt 5 ] && rank0_layers=5
    local remaining=$((TOTAL_LAYERS - rank0_layers))
    local others=$((n - 1))
    local per_other=$((remaining / others))
    local leftover=$((remaining % others))
    local lw="$rank0_layers"
    for i in $(seq 1 $others); do
        local extra=0
        [ "$((i - 1))" -lt "$leftover" ] && extra=1
        lw="${lw},$((per_other + extra))"
    done
    echo "$lw"
}

deploy_binaries() {
    echo "Deploying binaries to phones..."
    local rank0_ip="${PHONES[0]}"

    # Deploy cellswarm-master + mdns-advertise to rank 0
    echo "  Deploying cellswarm-master to $rank0_ip..."
    $ADB -s "${rank0_ip}:5555" push "$BIN_DIR/cellswarm-master" /data/local/tmp/cellswarm/bin/cellswarm-master
    $ADB -s "${rank0_ip}:5555" push "$BIN_DIR/mdns-advertise" /data/local/tmp/cellswarm/bin/mdns-advertise
    adb_shell "$rank0_ip" "chmod +x /data/local/tmp/cellswarm/bin/cellswarm-master /data/local/tmp/cellswarm/bin/mdns-advertise"

    # Deploy cellswarm-worker to all phones (rank 0 also needs it as fallback)
    for ip in "${PHONES[@]}"; do
        echo "  Deploying cellswarm-worker to $ip..."
        $ADB -s "${ip}:5555" push "$BIN_DIR/cellswarm-worker" /data/local/tmp/cellswarm/bin/cellswarm-worker 2>/dev/null || true
        adb_shell "$ip" "chmod +x /data/local/tmp/cellswarm/bin/cellswarm-worker" 2>/dev/null || true
    done
    echo "  Deploy complete."
}

echo "============================================"
echo " cellswarm-master — Phone Ring Launcher"
echo "============================================"
echo "  Phones:     $N_PHONES (${PHONES[0]} .. ${PHONES[$((N_PHONES-1))]})"
echo "  Model:      $(basename "$MODEL_REMOTE")"
echo "  Draft:      $(basename "$DRAFT_REMOTE") (max=$DRAFT_MAX)"
echo "  HTTP port:  $HTTP_PORT"
echo "  Context:    $CTX_SIZE"
echo "  Threads:    $THREADS"
echo ""

trap cleanup EXIT

# Optionally deploy
if $DEPLOY; then
    deploy_binaries
fi

# Verify phones
echo "Verifying phones..."
ok=0
for ip in "${PHONES[@]}"; do
    if $ADB -s "${ip}:5555" shell "echo ok" 2>/dev/null | grep -q ok; then
        ok=$((ok + 1))
    else
        echo "  WARNING: $ip unreachable!"
    fi
done
echo "  $ok/$N_PHONES phones online."
[ "$ok" -eq "$N_PHONES" ] || { echo "ERROR: Not all phones reachable!"; exit 1; }

# Clean up any existing processes
cleanup 2>/dev/null || true
sleep 1

# Calculate layer distribution
LW=$(calc_layer_weights "$N_PHONES")
echo "Layer weights: $LW"
echo ""

RANK0_IP="${PHONES[0]}"

# Start worker phones (rank 1 to N-1)
echo "Starting workers (rank 1-$((N_PHONES-1)))..."
for idx in $(seq 1 $((N_PHONES - 1))); do
    ip="${PHONES[$idx]}"
    next_idx=$(( (idx + 1) % N_PHONES ))
    next_ip="${PHONES[$next_idx]}"

    adb_shell "$ip" "sh -c '
cd /data/local/tmp
taskset $TASKSET ./cellswarm/bin/cellswarm-worker \
  -m $MODEL_REMOTE \
  --world $N_PHONES --rank $idx \
  --master $RANK0_IP --next $next_ip \
  --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
  -lw $LW -c $CTX_SIZE -n -1 -t $THREADS -tb $THREADS \
  --no-mmap --prefetch $EXTRA_FLAGS \
  > /data/local/tmp/cellswarm-worker.log 2>&1 &
'" 2>/dev/null
    echo "  rank $idx on $ip (next=$next_ip)"
done

echo ""
echo "Waiting for workers to load model (120s for --no-mmap)..."
sleep 120

# Verify workers
all_ok=true
for idx in $(seq 1 $((N_PHONES - 1))); do
    ip="${PHONES[$idx]}"
    pid=$(adb_shell "$ip" "pidof cellswarm-worker" 2>/dev/null | tr -d '\r' || true)
    if [ -n "$pid" ]; then
        echo "  $ip (rank $idx): running (pid $pid)"
    else
        echo "  $ip (rank $idx): NOT RUNNING!"
        adb_shell "$ip" "tail -20 /data/local/tmp/cellswarm-worker.log" 2>/dev/null || true
        all_ok=false
    fi
done

if ! $all_ok; then
    echo "ERROR: Some workers failed to start!"
    exit 1
fi

# Start master (rank 0) — HTTP server + ring coordinator
NEXT_IP="${PHONES[1]}"
echo ""
echo "Starting cellswarm-master on $RANK0_IP (rank 0, next=$NEXT_IP)..."
adb_shell "$RANK0_IP" "sh -c '
cd /data/local/tmp
SWARM_BATCH_PIPELINE=1 SWARM_OVERLAP_DRAFT=$OVERLAP_DRAFT taskset $TASKSET ./cellswarm/bin/cellswarm-master \
  -m $MODEL_REMOTE \
  --model-draft $DRAFT_REMOTE \
  --draft-max $DRAFT_MAX \
  --world $N_PHONES --rank 0 \
  --master $RANK0_IP --next $NEXT_IP \
  --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
  -lw $LW -c $CTX_SIZE -t $THREADS -tb $THREADS \
  --no-mmap --prefetch \
  --host 0.0.0.0 --port $HTTP_PORT \
  -np 1 $EXTRA_FLAGS \
  > /data/local/tmp/cellswarm-master.log 2>&1 &
'" 2>/dev/null

echo "  Master started. Waiting for HTTP server..."
sleep 10

# Verify master is running
master_pid=$(adb_shell "$RANK0_IP" "pidof cellswarm-master" 2>/dev/null | tr -d '\r' || true)
if [ -z "$master_pid" ]; then
    echo "ERROR: Master failed to start!"
    echo "--- Master log ---"
    adb_shell "$RANK0_IP" "cat /data/local/tmp/cellswarm-master.log" 2>/dev/null || true
    exit 1
fi

# Start mDNS advertisement — cellswarm.local → master phone IP
echo "Starting mDNS: cellswarm.local → $RANK0_IP"
adb_shell "$RANK0_IP" "pkill -9 mdns-advertise" 2>/dev/null || true
adb_shell "$RANK0_IP" "sh -c '
cd /data/local/tmp
./cellswarm/bin/mdns-advertise cellswarm $RANK0_IP > /data/local/tmp/mdns-advertise.log 2>&1 &
'" 2>/dev/null
sleep 1
mdns_pid=$(adb_shell "$RANK0_IP" "pidof mdns-advertise" 2>/dev/null | tr -d '\r' || true)
if [ -n "$mdns_pid" ]; then
    echo "  mDNS running (pid $mdns_pid) — http://cellswarm.local:${HTTP_PORT}/"
else
    echo "  WARNING: mDNS failed to start (dashboard still works via IP)"
fi

echo ""
echo "============================================"
echo " cellswarm-master is LIVE"
echo "============================================"
echo ""
echo "  Dashboard:  http://cellswarm.local:${HTTP_PORT}/"
echo "              http://${RANK0_IP}:${HTTP_PORT}/"
echo "  API:        http://cellswarm.local:${HTTP_PORT}/v1/chat/completions"
echo "  Ring info:  http://cellswarm.local:${HTTP_PORT}/api/ring"
echo "  Spec stats: http://cellswarm.local:${HTTP_PORT}/api/spec"
echo ""
echo "  Master PID: $master_pid on $RANK0_IP"
echo "  Ring size:  $N_PHONES phones"
echo "  Layers:     $LW"
echo ""
echo "  To stop:    Ctrl+C (or kill this script)"
echo "  Master log: adb -s ${RANK0_IP}:5555 shell cat /data/local/tmp/cellswarm-master.log"
echo ""

# Keep alive — tail the master log
echo "--- Master log (streaming) ---"
while true; do
    adb_shell "$RANK0_IP" "tail -f /data/local/tmp/cellswarm-master.log" 2>/dev/null || true
    sleep 2
done
