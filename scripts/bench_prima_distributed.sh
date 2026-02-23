#!/bin/bash
# Distributed prima.cpp benchmark via SSH/Termux
# Tests the 33B model across N phones in a ring topology
#
# Usage: bash scripts/bench_prima_distributed.sh [N_PHONES] [N_TOKENS]
# Default: 11 phones, 64 tokens

N_PHONES=${1:-11}
N_TOKENS=${2:-64}
MODEL="/data/local/tmp/adb-llm/models/deepseek-coder-33b-instruct.Q4_K_M.gguf"
PROMPT="Write a hello world program in Python"
DATA_PORT=${3:-9100}
SIGNAL_PORT=${4:-10100}
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -p 8022"

# All available phones (ordered by IP, excluding 10.105.0.12 - SSH down from OOM)
ALL_PHONES=(
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
    10.105.0.41
    10.105.0.42
    10.105.0.44
    10.105.0.45
    10.105.0.48
    10.105.0.156
)

# Select first N phones
PHONES=("${ALL_PHONES[@]:0:$N_PHONES}")

# Rank 0 must be 10.105.0.13 (has HiGHS built)
RANK0="10.105.0.13"
# Build ring excluding rank 0, then prepend it
WORKER_PHONES=()
for p in "${PHONES[@]}"; do
    [ "$p" != "$RANK0" ] && WORKER_PHONES+=("$p")
done
# Ring order: rank0, worker1, worker2, ...
RING=("$RANK0" "${WORKER_PHONES[@]:0:$((N_PHONES-1))}")
WORLD=${#RING[@]}

# Calculate layer distribution (62 layers for 33B model)
N_LAYERS=62
LAYERS_PER_PHONE=$((N_LAYERS / WORLD))
EXTRA_LAYERS=$((N_LAYERS % WORLD))
LAYER_WINDOW=""
for ((i=0; i<WORLD; i++)); do
    if [ $i -lt $EXTRA_LAYERS ]; then
        n=$((LAYERS_PER_PHONE + 1))
    else
        n=$LAYERS_PER_PHONE
    fi
    [ -n "$LAYER_WINDOW" ] && LAYER_WINDOW="${LAYER_WINDOW},"
    LAYER_WINDOW="${LAYER_WINDOW}${n}"
done

echo "============================================"
echo " Prima.cpp Distributed Benchmark"
echo "============================================"
echo " Model:  deepseek-coder-33b-instruct.Q4_K_M.gguf"
echo " Phones: ${RING[*]}"
echo " World:  $WORLD"
echo " Tokens: $N_TOKENS"
echo " Layers: $LAYER_WINDOW (total=$N_LAYERS)"
echo " Ports:  data=$DATA_PORT signal=$SIGNAL_PORT"
echo "============================================"
echo ""

# Step 1: Kill any existing prima.cpp / cellswarm processes
echo "[1/4] Cleaning up old processes..."
for ip in "${RING[@]}"; do
    ssh $SSH_OPTS "$ip" "pkill -9 llama-cli 2>/dev/null; pkill -9 llama-server 2>/dev/null; pkill -9 cellswarm 2>/dev/null" 2>/dev/null || true
done
echo "  Waiting 10s for sockets to release..."
sleep 10
echo "  Done."
echo ""

# Step 2: Verify all phones reachable
echo "[2/4] Verifying phones..."
online=0
for ip in "${RING[@]}"; do
    if ssh $SSH_OPTS "$ip" "echo ok" 2>/dev/null | grep -q ok; then
        echo "  $ip: ✓"
        ((online++))
    else
        echo "  $ip: ✗ UNREACHABLE"
    fi
done
echo "  $online/$WORLD phones online."
if [ "$online" -lt "$WORLD" ]; then
    echo "ERROR: Not all phones are reachable. Aborting."
    exit 1
fi
echo ""

# Step 3: Start workers (ranks 1..N-1), then rank 0
echo "[3/4] Starting workers..."
WORKER_PIDS=()

# Start workers (non-rank-0)
for ((i=1; i<WORLD; i++)); do
    ip="${RING[$i]}"
    next_idx=$(( (i+1) % WORLD ))
    next_ip="${RING[$next_idx]}"
    master_ip="$RANK0"

    echo "  Starting rank $i on $ip (next=$next_ip, master=$master_ip)..."
    # Keep SSH session alive in background — process dies if SSH disconnects
    ssh $SSH_OPTS "$ip" "cd ~/prima.cpp && \
        taskset f0 ./llama-cli \
            -m $MODEL \
            --world $WORLD --rank $i \
            --master $master_ip --next $next_ip \
            --layer-window $LAYER_WINDOW \
            --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
            --prefetch -t 4 \
            2>&1 | tee /tmp/prima-rank$i.log" &
    WORKER_PIDS+=($!)
    sleep 1  # stagger launches
    echo "    launched (ssh pid $!)."
done

echo ""
echo "  Waiting 30s for workers to load model (mmap)..."
sleep 30

# Verify workers are running (check local SSH background jobs)
echo "  Checking workers..."
alive=0
for ((i=0; i<${#WORKER_PIDS[@]}; i++)); do
    pid=${WORKER_PIDS[$i]}
    rank=$((i+1))
    ip="${RING[$rank]}"
    if kill -0 $pid 2>/dev/null; then
        echo "    rank $rank ($ip): ✓ running (local ssh pid $pid)"
        ((alive++))
    else
        echo "    rank $rank ($ip): ✗ not running"
    fi
done
echo "  $alive/$((WORLD-1)) workers alive."
echo ""

# Step 4: Start rank 0 and capture output
echo "[4/4] Starting rank 0 on $RANK0..."
next_ip="${RING[1]}"

echo "  Rank 0: world=$WORLD, next=$next_ip"
echo "  Generating $N_TOKENS tokens..."
echo ""

# Run rank 0 (blocking) with timeout
timeout 600 ssh $SSH_OPTS "$RANK0" "cd ~/prima.cpp && \
    LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib:\$LD_LIBRARY_PATH \
    taskset f0 ./llama-cli \
        -m $MODEL \
        -c 512 -n $N_TOKENS \
        -p '$PROMPT' \
        --world $WORLD --rank 0 \
        --master $RANK0 --next $next_ip \
        --layer-window $LAYER_WINDOW \
        --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
        --prefetch -t 4 \
        2>&1" | tee /tmp/prima-distributed-${WORLD}phone.log

echo ""
echo "============================================"
echo " Results"
echo "============================================"

# Extract metrics
if grep -q "eval time" /tmp/prima-distributed-${WORLD}phone.log 2>/dev/null; then
    echo ""
    grep -E "(prompt eval|eval time|total time|tok/s)" /tmp/prima-distributed-${WORLD}phone.log
    echo ""
    echo "Log saved: /tmp/prima-distributed-${WORLD}phone.log"
else
    echo "No performance metrics found in output."
    echo "Check /tmp/prima-distributed-${WORLD}phone.log for details."
fi
echo "============================================"

# Cleanup
echo ""
echo "Cleaning up workers..."
# Kill local SSH background jobs
for pid in "${WORKER_PIDS[@]}"; do
    kill $pid 2>/dev/null || true
done
# Kill remote llama-cli processes
for ip in "${RING[@]}"; do
    ssh $SSH_OPTS "$ip" "pkill -9 llama-cli 2>/dev/null" 2>/dev/null || true
done
echo "Done."
