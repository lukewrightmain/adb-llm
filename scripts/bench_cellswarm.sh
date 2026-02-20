#!/usr/bin/env bash
# Quick benchmark for cellswarm pipeline-ring inference.
#
# Usage:
#   ./scripts/bench_cellswarm.sh 2        # 2-node (1 phone), CPU only
#   ./scripts/bench_cellswarm.sh 2 20     # 2-node, 20 GPU layers on phones (Vulkan)
#   ./scripts/bench_cellswarm.sh 4        # 4-node (3 phones), CPU only
#   ./scripts/bench_cellswarm.sh 4 10     # 4-node, 10 GPU layers on phones
#
# Environment variables:
#   ACT_QUANT=fp16  — enable FP16 activation quantization (halves transfer size)
#
# Prerequisites: phones have cellswarm-worker + model already deployed.

set -euo pipefail

WORLD_SIZE=${1:-2}
NGL=${2:-0}  # GPU layers to offload on phone workers (0=CPU only)
N_PHONES=$((WORLD_SIZE - 1))
ACT_QUANT="${ACT_QUANT:-fp32}"  # fp32 or fp16

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SWARM_HOST="$PROJECT_DIR/bin/cellswarm-host"
ADB='"C:\Program Files\platform-tools\adb.exe"'
SSH_HOST="winpc"
MODEL_REMOTE="/data/local/tmp/cellswarm/models/deepseek-coder-33b-instruct.Q4_K_M.gguf"
MODEL_LOCAL="$HOME/models/deepseek-coder-33b-instruct.Q4_K_M.gguf"

# Bottom phones from adb devices list (avoid conflict with other agents using top)
ALL_PHONES=(RFCRB0BYFAX RFCRB0B1N6Y RFCRA19TM7Y RFCR80H5CRZ)
PHONES=("${ALL_PHONES[@]:0:$N_PHONES}")

TOTAL_LAYERS=62  # DeepSeek Coder 33B

# Ports
DATA_PORT=9000
SIGNAL_PORT=10000
FWD_BASE=59001
REV_BASE=58001
SIG_OFFSET=100

echo "============================================"
echo " cellswarm benchmark — ${WORLD_SIZE}-node ring"
echo "============================================"
echo "  Phones: ${PHONES[*]}"
if [ "$NGL" -gt 0 ]; then
    echo "  GPU layers (Vulkan): $NGL per phone"
else
    echo "  GPU layers: none (CPU only)"
fi
echo ""

cleanup() {
    echo "Cleaning up..."
    # Kill cellswarm-workers and remove ADB tunnels
    for SERIAL in "${ALL_PHONES[@]}"; do
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell pkill -f cellswarm-worker" 2>/dev/null || true
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL forward --remove-all" 2>/dev/null || true
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL reverse --remove-all" 2>/dev/null || true
    done
    # Kill SSH tunnels on cellswarm ports (9001+, 10001+, 58xxx, 59xxx)
    for port in $(seq $DATA_PORT $((DATA_PORT + 10))) $(seq $SIGNAL_PORT $((SIGNAL_PORT + 10))); do
        fuser -k "${port}/tcp" 2>/dev/null || true
    done
    # Kill cellswarm-host
    pkill -f cellswarm-host 2>/dev/null || true
    rm -f /tmp/ssh-cellswarm-* 2>/dev/null
    echo "Cleanup done."
}

trap cleanup EXIT

# Clean existing state
cleanup 2>/dev/null || true
sleep 1

# Calculate layer distribution
# Host is ~2x faster per-layer than phone (4.8ms vs 10ms), so host gets more layers.
# For 2-node: host=42, phone=20 (balanced at ~200ms each) — proven optimal.
# For N-node: host=42, remaining 20 split among phones.
HOST_LAYERS=42
PHONE_LAYERS=$((TOTAL_LAYERS - HOST_LAYERS))
LAYERS_PER_PHONE=$((PHONE_LAYERS / N_PHONES))
REMAINDER=$((PHONE_LAYERS % N_PHONES))
LW="$HOST_LAYERS"
for i in $(seq 0 $((N_PHONES - 1))); do
    EXTRA=0
    if [ "$i" -lt "$REMAINDER" ]; then
        EXTRA=1
    fi
    LW="${LW},$((LAYERS_PER_PHONE + EXTRA))"
done
echo "Layer distribution: $LW"

# --- Set up tunnels ---
# cellswarm port layout:
#   Each rank N binds PULL on data_port+N and signal_port+N
#   Each rank N connects PUSH to next_ip:(data_port + next_rank)
#   So tunnels must map cellswarm's expected ports, not arbitrary ones.
echo ""
echo "Setting up tunnels..."

for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    RANK=$((idx + 1))

    # Phone binds on: data_port+rank, signal_port+rank
    PHONE_DATA_BIND=$((DATA_PORT + RANK))
    PHONE_SIGNAL_BIND=$((SIGNAL_PORT + RANK))

    # WinPC-side ADB forward ports (intermediate, can be anything unique)
    WIN_FWD_DATA=$((FWD_BASE + idx))
    WIN_FWD_SIG=$((FWD_BASE + SIG_OFFSET + idx))

    # Step 1: ADB forward on WinPC: WinPC:WIN_FWD → phone:PHONE_BIND
    echo "  [$SERIAL] ADB forward: winpc:$WIN_FWD_DATA -> phone:$PHONE_DATA_BIND"
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL forward tcp:$WIN_FWD_DATA tcp:$PHONE_DATA_BIND"
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL forward tcp:$WIN_FWD_SIG tcp:$PHONE_SIGNAL_BIND"

    # Step 2: SSH -L tunnel: host:PHONE_BIND → WinPC:WIN_FWD → phone:PHONE_BIND
    # Local port MUST match what cellswarm expects (data_port + rank)
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

    # Phone connects PUSH to localhost:(data_port + next_rank)
    PHONE_CONNECT_DATA=$((DATA_PORT + NEXT_RANK))
    PHONE_CONNECT_SIG=$((SIGNAL_PORT + NEXT_RANK))

    if [ "$NEXT_RANK" -eq 0 ]; then
        # Last phone -> Host: needs SSH -R tunnel + ADB reverse
        # Chain: phone:PHONE_CONNECT → ADB reverse → WinPC:REV_BASE+idx → SSH -R → host:DATA_PORT
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
        # Phone -> Phone: DIRECT via WinPC (no SSH!)
        # Chain: phone:PHONE_CONNECT → ADB reverse → WinPC:WIN_FWD_of_next → ADB forward → next_phone
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

# --- Start cellswarm-workers on phones ---
echo ""
echo "Starting cellswarm-workers..."
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    RANK=$((idx + 1))

    # Write startup script locally, push to phone, then execute.
    # This avoids Windows CMD mangling > & | characters.
    SCRIPT_LOCAL="/tmp/cellswarm-start-rank${RANK}.sh"
    # Build GPU layers flag (only if NGL > 0)
    NGL_FLAG=""
    if [ "$NGL" -gt 0 ]; then
        NGL_FLAG="-ngl $NGL"
    fi
    ACT_FLAG=""
    if [ "$ACT_QUANT" = "fp16" ]; then
        ACT_FLAG="--act-quant fp16"
    fi

    cat > "$SCRIPT_LOCAL" << ENDSCRIPT
#!/system/bin/sh
cd /data/local/tmp
./cellswarm/bin/cellswarm-worker \
  -m $MODEL_REMOTE \
  --world $WORLD_SIZE --rank $RANK \
  --master 127.0.0.1 --next 127.0.0.1 \
  --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
  -lw $LW -c 512 -n -1 \
  --prefetch $NGL_FLAG $ACT_FLAG \
  > /data/local/tmp/cellswarm-worker.log 2>&1 &
ENDSCRIPT

    SCRIPT_WIN="C:\\Users\\Lukio-4090\\cellswarm-start-rank${RANK}.sh"
    SCRIPT_PHONE="/data/local/tmp/cellswarm-start.sh"

    # SCP script to Windows, then adb push to phone
    scp -o ConnectTimeout=10 "$SCRIPT_LOCAL" "${SSH_HOST}:${SCRIPT_WIN}" >/dev/null 2>&1
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL push \"$SCRIPT_WIN\" $SCRIPT_PHONE" >/dev/null 2>&1
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell chmod 755 $SCRIPT_PHONE" 2>/dev/null

    # Run the script on the phone (the script itself backgrounds the worker)
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell sh $SCRIPT_PHONE" 2>/dev/null
    echo "  Started rank $RANK on $SERIAL"
done

echo "Waiting for workers to initialize (30s)..."
sleep 30

# Verify workers are running
for idx in $(seq 0 $((N_PHONES - 1))); do
    SERIAL="${PHONES[$idx]}"
    PID=$(ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell pidof cellswarm-worker" 2>/dev/null || true)
    if [ -n "$PID" ]; then
        echo "  $SERIAL: cellswarm-worker running (pid $PID)"
    else
        echo "  $SERIAL: WARNING — cellswarm-worker NOT running!"
        echo "  Log:"
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell tail -20 /data/local/tmp/cellswarm-worker.log" 2>/dev/null || true
    fi
done

# --- Run cellswarm-host benchmark ---
echo ""
GPU_INFO=""
if [ "$NGL" -gt 0 ]; then
    GPU_INFO=" + ${NGL} GPU layers (Vulkan)"
fi
ACT_INFO=""
if [ "$ACT_QUANT" = "fp16" ]; then
    ACT_INFO=" + FP16 activations"
fi
HOST_ACT_FLAG=""
if [ "$ACT_QUANT" = "fp16" ]; then
    HOST_ACT_FLAG="--act-quant fp16"
fi
echo "============================================"
echo " Running benchmark: $WORLD_SIZE-node, 128 tokens${GPU_INFO}${ACT_INFO}"
echo "============================================"

# cellswarm-host is rank 0
time "$SWARM_HOST" \
    -m "$MODEL_LOCAL" \
    --world "$WORLD_SIZE" --rank 0 \
    --next 127.0.0.1 --master 127.0.0.1 \
    --data-port "$DATA_PORT" --signal-port "$SIGNAL_PORT" \
    -lw "$LW" -c 512 -t 48 \
    --prefetch $HOST_ACT_FLAG \
    -p "Write a Python function that computes the Fibonacci sequence efficiently using dynamic programming." \
    -n 128 2>&1 | tee /tmp/cellswarm-bench-${WORLD_SIZE}node.log

echo ""
echo "============================================"
echo " Benchmark complete. Log: /tmp/cellswarm-bench-${WORLD_SIZE}node.log"
echo "============================================"
