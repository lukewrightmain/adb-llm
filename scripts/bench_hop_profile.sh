#!/bin/bash
# bench_hop_profile.sh — Profile ZMQ hop overhead on ethernet phones
#
# Runs a short inference with PRIMA_HOP_PROFILE=1 to collect per-hop
# timing breakdown, then summarizes the results.
#
# Usage:
#   ./scripts/bench_hop_profile.sh <N_PHONES> [--raw-tcp] [--tokens N]
#
# Options:
#   N_PHONES    Number of phones (2-20)
#   --raw-tcp   Also run raw TCP hop benchmark between phone pairs
#   --tokens N  Generate N tokens (default 32, enough for ~2-3 cycles)
#
# Prerequisites:
#   - prima-worker-spec deployed to phones
#   - Model deployed to phones
#   - For --raw-tcp: tcp_hop_bench deployed to phones
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ADB="${ADB:-$HOME/.local/bin/adb}"

# Ethernet phone IPs (first 20)
ALL_PHONES=(
    10.105.0.12 10.105.0.13 10.105.0.17 10.105.0.19 10.105.0.20
    10.105.0.24 10.105.0.28 10.105.0.29 10.105.0.30 10.105.0.31
    10.105.0.32 10.105.0.36 10.105.0.38 10.105.0.40 10.105.0.41
    10.105.0.42 10.105.0.44 10.105.0.45 10.105.0.48 10.105.0.156
)

N_PHONES="${1:?Usage: $0 <N_PHONES> [--raw-tcp] [--tokens N]}"
shift

RUN_RAW_TCP=0
GEN_TOKENS=32

while [[ $# -gt 0 ]]; do
    case "$1" in
        --raw-tcp) RUN_RAW_TCP=1; shift ;;
        --tokens)  GEN_TOKENS="$2"; shift 2 ;;
        *)         echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if (( N_PHONES < 2 || N_PHONES > ${#ALL_PHONES[@]} )); then
    echo "ERROR: N_PHONES must be 2-${#ALL_PHONES[@]}"
    exit 1
fi

PHONES=("${ALL_PHONES[@]:0:$N_PHONES}")
PHONE_LIST=$(IFS=,; echo "${PHONES[*]}")
echo "=== Hop Profile Benchmark ==="
echo "Phones: $N_PHONES ($PHONE_LIST)"
echo "Tokens: $GEN_TOKENS"
echo ""

# ─── Part 1: ZMQ Hop Profile ───
echo "── Part 1: ZMQ Hop Profile (PRIMA_HOP_PROFILE=1) ──"
echo ""

PROFILE_DIR="/tmp/hop_profile_$$"
mkdir -p "$PROFILE_DIR"

# Build the ring endpoint list
RING_ENDPOINTS=""
BASE_PORT=50000
for i in $(seq 0 $((N_PHONES - 1))); do
    if [ -n "$RING_ENDPOINTS" ]; then
        RING_ENDPOINTS="$RING_ENDPOINTS,"
    fi
    RING_ENDPOINTS="${RING_ENDPOINTS}${PHONES[$i]}:$((BASE_PORT + i))"
done

echo "Ring: $RING_ENDPOINTS"
echo ""

# Start workers (rank 1..N-1) in background
WORKER_PIDS=()
for rank in $(seq 1 $((N_PHONES - 1))); do
    IP="${PHONES[$rank]}"
    echo "Starting worker rank=$rank on $IP..."
    ssh -o StrictHostKeyChecking=no -p 8022 "root@$IP" \
        "cd /data/local/tmp/adb-llm && \
         PRIMA_HOP_PROFILE=1 \
         taskset f0 ./bin/prima-worker-spec \
            --rank $rank --world $N_PHONES \
            --ring '$RING_ENDPOINTS' \
            --port $BASE_PORT \
            -t 4 --no-mmap \
            2>'$PROFILE_DIR/rank${rank}.log'" \
        &>/dev/null &
    WORKER_PIDS+=($!)
done

sleep 2  # Let workers bind

# Run host (rank 0) — collect its profile output
echo "Starting host rank=0 on ${PHONES[0]}..."
HOST_IP="${PHONES[0]}"
ssh -o StrictHostKeyChecking=no -p 8022 "root@$HOST_IP" \
    "cd /data/local/tmp/adb-llm && \
     PRIMA_HOP_PROFILE=1 \
     taskset f0 ./bin/prima-worker-spec \
        --rank 0 --world $N_PHONES \
        --ring '$RING_ENDPOINTS' \
        --port $BASE_PORT \
        -t 4 --no-mmap \
        -n $GEN_TOKENS \
        -p 'Write a short poem about phones.' \
        2>&1" \
    | tee "$PROFILE_DIR/rank0.log"

# Wait for workers to finish
sleep 2
for pid in "${WORKER_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
done

# Collect worker logs
echo ""
echo "── Collecting worker logs ──"
for rank in $(seq 1 $((N_PHONES - 1))); do
    IP="${PHONES[$rank]}"
    scp -o StrictHostKeyChecking=no -P 8022 \
        "root@$IP:$PROFILE_DIR/rank${rank}.log" \
        "$PROFILE_DIR/rank${rank}.log" 2>/dev/null || true
done

# Parse and summarize
echo ""
echo "══════════════════════════════════════════"
echo "  HOP PROFILE SUMMARY ($N_PHONES phones)"
echo "══════════════════════════════════════════"
echo ""

for rank in $(seq 0 $((N_PHONES - 1))); do
    LOG="$PROFILE_DIR/rank${rank}.log"
    if [ ! -f "$LOG" ]; then
        echo "  rank $rank: no log file"
        continue
    fi

    SEND_COUNT=$(grep -c '\[HOP-PROFILE\] SEND' "$LOG" 2>/dev/null || echo 0)
    RECV_COUNT=$(grep -c '\[HOP-PROFILE\] RECV' "$LOG" 2>/dev/null || echo 0)

    if (( SEND_COUNT > 0 )); then
        SEND_AVG=$(grep '\[HOP-PROFILE\] SEND' "$LOG" | \
            awk -F'total=' '{print $2}' | awk -F'us' '{sum+=$1; n++} END {printf "%.0f", sum/n}')
        SEND_ZMQ=$(grep '\[HOP-PROFILE\] SEND' "$LOG" | \
            awk -F'zmq_send=' '{print $2}' | awk -F'us' '{sum+=$1; n++} END {printf "%.0f", sum/n}')
        echo "  rank $rank SEND: n=$SEND_COUNT avg_total=${SEND_AVG}us avg_zmq_send=${SEND_ZMQ}us"
    fi

    if (( RECV_COUNT > 0 )); then
        RECV_AVG=$(grep '\[HOP-PROFILE\] RECV' "$LOG" | \
            awk -F'total=' '{print $2}' | awk -F'us' '{sum+=$1; n++} END {printf "%.0f", sum/n}')
        RECV_ZMQ=$(grep '\[HOP-PROFILE\] RECV' "$LOG" | \
            awk -F'zmq_recv=' '{print $2}' | awk -F'us' '{sum+=$1; n++} END {printf "%.0f", sum/n}')
        echo "  rank $rank RECV: n=$RECV_COUNT avg_total=${RECV_AVG}us avg_zmq_recv=${RECV_ZMQ}us"
    fi
done

echo ""
echo "Raw logs in: $PROFILE_DIR/"

# ─── Part 2: Raw TCP Hop Benchmark ───
if (( RUN_RAW_TCP )); then
    echo ""
    echo "── Part 2: Raw TCP Hop Benchmark ──"
    echo ""

    PHONE_A="${PHONES[0]}"
    PHONE_B="${PHONES[1]}"
    TCP_PORT=9000

    echo "Testing raw TCP: $PHONE_A → $PHONE_B (28696 bytes × 1000 rounds)"
    echo ""

    # Start server on phone B
    ssh -o StrictHostKeyChecking=no -p 8022 "root@$PHONE_B" \
        "/data/local/tmp/adb-llm/bin/tcp_hop_bench -s -p $TCP_PORT -n 1000" &
    SERVER_PID=$!
    sleep 1

    # Run client on phone A
    ssh -o StrictHostKeyChecking=no -p 8022 "root@$PHONE_A" \
        "/data/local/tmp/adb-llm/bin/tcp_hop_bench -c $PHONE_B -p $TCP_PORT -n 1000"

    wait "$SERVER_PID" 2>/dev/null || true

    # If 3+ phones, test ring forwarding
    if (( N_PHONES >= 3 )); then
        PHONE_C="${PHONES[2]}"
        echo ""
        echo "Testing 3-phone ring hop: $PHONE_A → $PHONE_B → $PHONE_C"
        echo ""

        # Server on phone C (final destination)
        ssh -o StrictHostKeyChecking=no -p 8022 "root@$PHONE_C" \
            "/data/local/tmp/adb-llm/bin/tcp_hop_bench -s -p $TCP_PORT -n 1000" &
        C_PID=$!
        sleep 1

        # Ring forwarder on phone B
        ssh -o StrictHostKeyChecking=no -p 8022 "root@$PHONE_B" \
            "/data/local/tmp/adb-llm/bin/tcp_hop_bench -r $TCP_PORT $PHONE_C $TCP_PORT -n 1000" &
        B_PID=$!
        sleep 1

        # Client on phone A
        ssh -o StrictHostKeyChecking=no -p 8022 "root@$PHONE_A" \
            "/data/local/tmp/adb-llm/bin/tcp_hop_bench -c $PHONE_B -p $TCP_PORT -n 1000"

        wait "$B_PID" 2>/dev/null || true
        wait "$C_PID" 2>/dev/null || true
    fi
fi

echo ""
echo "Done."
