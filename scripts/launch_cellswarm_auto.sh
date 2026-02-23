#!/usr/bin/env bash
# Zero-config auto-discovery launcher for cellswarm phone ring.
#
# Discovers phones via ADB, scans models, elects master, forms ring.
# No arguments needed — just run it.
#
# Usage:
#   ./scripts/launch_cellswarm_auto.sh                    # full auto
#   ./scripts/launch_cellswarm_auto.sh --dry-run           # show plan, don't launch
#   ./scripts/launch_cellswarm_auto.sh --min-phones 8      # require at least 8
#   ./scripts/launch_cellswarm_auto.sh --port 9090         # custom HTTP port
#   ./scripts/launch_cellswarm_auto.sh --no-spec            # skip speculative decode
#   ./scripts/launch_cellswarm_auto.sh --draft-max 32       # custom draft-max tokens
#   ./scripts/launch_cellswarm_auto.sh --ctx 2048           # context size
#
# Prerequisites:
#   - cellswarm-master + cellswarm-worker binaries built (run build_cellswarm.sh)
#   - Models deployed to phones (run deploy_model.sh)
#   - Phones reachable via ADB (ethernet or USB)

set -euo pipefail

# ==========================
# CLI argument parsing
# ==========================
DRY_RUN=false
MIN_PHONES=2
HTTP_PORT=8080
NO_SPEC=false
DRAFT_MAX=24
CTX_SIZE=2048
THREADS=4
TASKSET="f0"
EXTRA_FLAGS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)       DRY_RUN=true ;;
        --min-phones)    shift; MIN_PHONES="$1" ;;
        --port)          shift; HTTP_PORT="$1" ;;
        --no-spec)       NO_SPEC=true ;;
        --draft-max)     shift; DRAFT_MAX="$1" ;;
        --ctx|-c)        shift; CTX_SIZE="$1" ;;
        -t)              shift; THREADS="$1" ;;
        --taskset)       shift; TASKSET="$1" ;;
        --extra)         shift; EXTRA_FLAGS="$1" ;;
        *)               echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

ADB="$HOME/.local/bin/adb"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="$PROJECT_DIR/bin"

# Randomize ports to avoid ZMQ zombie sockets from previous runs
DATA_PORT=$(( 9000 + RANDOM % 1000 ))
SIGNAL_PORT=$(( DATA_PORT + 1000 ))

MODEL_DIR="/data/local/tmp/cellswarm/models"
BIN_REMOTE="/data/local/tmp/cellswarm/bin"

# ==========================
# Phase 1: Discover phones
# ==========================
echo "============================================"
echo " cellswarm auto-discovery"
echo "============================================"
echo ""
echo "[Phase 1] Discovering phones via ADB..."

# Get all ADB-connected devices with IP addresses (ethernet phones at :5555)
mapfile -t RAW_IPS < <(
    $ADB devices -l 2>/dev/null \
    | grep -oP '10\.\d+\.\d+\.\d+(?=:5555)' \
    | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n
)

if [ ${#RAW_IPS[@]} -eq 0 ]; then
    echo "  ERROR: No ADB devices found!"
    echo "  Make sure phones are connected: adb devices"
    exit 1
fi
echo "  Found ${#RAW_IPS[@]} ADB devices."

# Filter to responsive phones (parallel ping)
echo "  Checking responsiveness..."
declare -a LIVE_PHONES=()
declare -A PHONE_STATUS=()

check_phone() {
    local ip=$1
    if $ADB -s "${ip}:5555" shell "echo ok" 2>/dev/null | grep -q ok; then
        echo "$ip"
    fi
}

# Run checks in parallel
declare -a PIDS=()
TMPDIR_CHECK=$(mktemp -d)
for ip in "${RAW_IPS[@]}"; do
    ( check_phone "$ip" > "$TMPDIR_CHECK/$ip" ) &
    PIDS+=($!)
done

# Wait for all checks
for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
done

# Collect results
for ip in "${RAW_IPS[@]}"; do
    result=$(cat "$TMPDIR_CHECK/$ip" 2>/dev/null || true)
    if [ -n "$result" ]; then
        LIVE_PHONES+=("$ip")
        PHONE_STATUS[$ip]="online"
    else
        PHONE_STATUS[$ip]="unreachable"
        echo "  WARNING: $ip unreachable — skipping"
    fi
done
rm -rf "$TMPDIR_CHECK"

echo "  ${#LIVE_PHONES[@]}/${#RAW_IPS[@]} phones responsive."

if [ ${#LIVE_PHONES[@]} -lt "$MIN_PHONES" ]; then
    echo "  ERROR: Need at least $MIN_PHONES phones, only ${#LIVE_PHONES[@]} available!"
    exit 1
fi

# ==========================
# Phase 2: Scan models
# ==========================
echo ""
echo "[Phase 2] Scanning models on each phone..."

declare -A MODEL_MAP=()     # ip → main model path
declare -A DRAFT_MAP=()     # ip → draft model path
declare -A MODEL_NAME=()    # ip → main model filename
declare -A DRAFT_NAME=()    # ip → draft model filename
declare -a PHONES_WITH_MODEL=()
declare -a PHONES_WITH_DRAFT=()
declare -A MODEL_COUNTS=()  # filename → count (for majority vote)

for ip in "${LIVE_PHONES[@]}"; do
    # List gguf files sorted by size (largest first), -L to dereference symlinks
    models_raw=$($ADB -s "${ip}:5555" shell "ls -lSL ${MODEL_DIR}/*.gguf 2>/dev/null" 2>/dev/null || true)

    if [ -z "$models_raw" ]; then
        echo "  $ip: no models — skipping"
        continue
    fi

    main_model=""
    main_name=""
    draft_model=""
    draft_name=""

    while IFS= read -r line; do
        # Parse ls -l output: perms links owner group SIZE date time filename
        size=$(echo "$line" | awk '{print $5}')
        filepath=$(echo "$line" | awk '{print $NF}')
        filename=$(basename "$filepath")

        [ -z "$size" ] && continue
        [ -z "$filepath" ] && continue

        # Main model: largest file > 5GB (5368709120 bytes)
        if [ -z "$main_model" ] && [ "$size" -gt 5368709120 ] 2>/dev/null; then
            main_model="$filepath"
            main_name="$filename"
        fi

        # Draft model: file < 2GB (2147483648 bytes)
        if [ -z "$draft_model" ] && [ "$size" -lt 2147483648 ] 2>/dev/null; then
            draft_model="$filepath"
            draft_name="$filename"
        fi
    done <<< "$models_raw"

    if [ -n "$main_model" ]; then
        MODEL_MAP[$ip]="$main_model"
        MODEL_NAME[$ip]="$main_name"
        PHONES_WITH_MODEL+=("$ip")
        MODEL_COUNTS[$main_name]=$(( ${MODEL_COUNTS[$main_name]:-0} + 1 ))
        status_str="main=$main_name"

        if [ -n "$draft_model" ]; then
            DRAFT_MAP[$ip]="$draft_model"
            DRAFT_NAME[$ip]="$draft_name"
            PHONES_WITH_DRAFT+=("$ip")
            status_str="$status_str, draft=$draft_name"
        fi

        echo "  $ip: $status_str"
    else
        echo "  $ip: no main model (>5GB) — skipping"
    fi
done

if [ ${#PHONES_WITH_MODEL[@]} -lt "$MIN_PHONES" ]; then
    echo ""
    echo "  ERROR: Only ${#PHONES_WITH_MODEL[@]} phones have a main model, need at least $MIN_PHONES!"
    exit 1
fi

# Majority vote: all phones must use the same main model
MAJORITY_MODEL=""
MAJORITY_COUNT=0
for name in "${!MODEL_COUNTS[@]}"; do
    if [ "${MODEL_COUNTS[$name]}" -gt "$MAJORITY_COUNT" ]; then
        MAJORITY_MODEL="$name"
        MAJORITY_COUNT="${MODEL_COUNTS[$name]}"
    fi
done

# Filter out phones with a different model
declare -a RING_PHONES=()
for ip in "${PHONES_WITH_MODEL[@]}"; do
    if [ "${MODEL_NAME[$ip]}" = "$MAJORITY_MODEL" ]; then
        RING_PHONES+=("$ip")
    else
        echo "  WARNING: $ip has ${MODEL_NAME[$ip]} (majority=$MAJORITY_MODEL) — excluding"
    fi
done

echo ""
echo "  Ring model:  $MAJORITY_MODEL (${#RING_PHONES[@]} phones)"

# ==========================
# Phase 3: Layer count detection
# ==========================
echo ""
echo "[Phase 3] Detecting layer count..."

detect_layers() {
    local model_name="$1"
    local name_lower=$(echo "$model_name" | tr '[:upper:]' '[:lower:]')

    # Lookup table for known models
    case "$name_lower" in
        *deepseek-coder-33b*|*deepseek*33b*)     echo 62 ;;
        *qwen2.5-coder-32b*|*qwen*32b*)          echo 64 ;;
        *qwen2.5-coder-7b*|*qwen*7b*)            echo 28 ;;
        *llama-3.1-8b*|*llama*8b*)               echo 32 ;;
        *llama-3.1-70b*|*llama*70b*)             echo 80 ;;
        *codellama-34b*|*codellama*34b*)          echo 48 ;;
        *mistral-7b*|*mistral*7b*)               echo 32 ;;
        *mixtral-8x7b*|*mixtral*8x7b*)           echo 32 ;;
        *phi-2*|*phi2*)                           echo 32 ;;
        *starcoder2-15b*|*starcoder*15b*)         echo 40 ;;
        *deepseek-coder-6.7b*|*deepseek*6.7b*)   echo 32 ;;
        *deepseek-coder-1.3b*|*deepseek*1.3b*)   echo 24 ;;
        *)
            echo ""  # Unknown — caller should handle
        ;;
    esac
}

TOTAL_LAYERS=$(detect_layers "$MAJORITY_MODEL")

if [ -z "$TOTAL_LAYERS" ]; then
    echo "  WARNING: Unknown model '$MAJORITY_MODEL' — cannot detect layer count."
    echo "  Attempting to parse from GGUF header on first phone..."

    # Try reading layer count from first phone's GGUF metadata
    first_ip="${RING_PHONES[0]}"
    model_path="${MODEL_MAP[$first_ip]}"

    # The GGUF metadata contains block_count; try to extract it
    # Fallback: ask user or use 62 as default
    layers_raw=$($ADB -s "${first_ip}:5555" shell "
        dd if='$model_path' bs=1 count=4096 2>/dev/null | strings | grep -oP 'block_count\x00+\K\d+' | head -1
    " 2>/dev/null || true)

    if [ -n "$layers_raw" ] && [ "$layers_raw" -gt 0 ] 2>/dev/null; then
        TOTAL_LAYERS="$layers_raw"
        echo "  Detected $TOTAL_LAYERS layers from GGUF header."
    else
        TOTAL_LAYERS=62
        echo "  WARNING: Could not detect layers — defaulting to $TOTAL_LAYERS"
    fi
else
    echo "  $MAJORITY_MODEL → $TOTAL_LAYERS layers"
fi

# ==========================
# Phase 4: Master election
# ==========================
echo ""
echo "[Phase 4] Electing master..."

MASTER_IP=""
SPEC_MODE=true

if $NO_SPEC; then
    SPEC_MODE=false
    MASTER_IP="${RING_PHONES[0]}"
    echo "  Speculative decoding disabled (--no-spec)."
    echo "  Master: $MASTER_IP (first phone)"
else
    # First phone (sorted by IP) with both main + draft = master
    for ip in "${RING_PHONES[@]}"; do
        if [ -n "${DRAFT_MAP[$ip]:-}" ]; then
            MASTER_IP="$ip"
            break
        fi
    done

    if [ -z "$MASTER_IP" ]; then
        echo "  WARNING: No phone has a draft model — disabling speculative decoding."
        SPEC_MODE=false
        MASTER_IP="${RING_PHONES[0]}"
        echo "  Master: $MASTER_IP (first phone, non-speculative)"
    else
        echo "  Master: $MASTER_IP (has draft: ${DRAFT_NAME[$MASTER_IP]})"
    fi
fi

# Reorder array so master is index 0
declare -a PHONES=("$MASTER_IP")
for ip in "${RING_PHONES[@]}"; do
    [ "$ip" = "$MASTER_IP" ] && continue
    PHONES+=("$ip")
done

N_PHONES=${#PHONES[@]}
MODEL_REMOTE="${MODEL_MAP[$MASTER_IP]}"
DRAFT_REMOTE="${DRAFT_MAP[$MASTER_IP]:-}"

echo "  Ring size: $N_PHONES phones"
echo "  Ring order: ${PHONES[*]}"

# ==========================
# Phase 5: Deploy missing binaries
# ==========================
echo ""
echo "[Phase 5] Checking binaries..."

deploy_count=0
for idx in "${!PHONES[@]}"; do
    ip="${PHONES[$idx]}"

    # Ensure directories exist
    $ADB -s "${ip}:5555" shell "mkdir -p ${BIN_REMOTE} ${MODEL_DIR}" 2>/dev/null || true

    if [ "$idx" -eq 0 ]; then
        # Master needs cellswarm-master + mdns-advertise + cellswarm-worker (fallback)
        for bin in cellswarm-master mdns-advertise cellswarm-worker; do
            has_bin=$($ADB -s "${ip}:5555" shell "test -x ${BIN_REMOTE}/${bin} && echo yes" 2>/dev/null | tr -d '\r' || true)
            if [ "$has_bin" != "yes" ]; then
                if [ -f "$BIN_DIR/$bin" ]; then
                    echo "  Deploying $bin to $ip..."
                    $ADB -s "${ip}:5555" push "$BIN_DIR/$bin" "${BIN_REMOTE}/${bin}" 2>/dev/null
                    $ADB -s "${ip}:5555" shell "chmod +x ${BIN_REMOTE}/${bin}" 2>/dev/null
                    deploy_count=$((deploy_count + 1))
                else
                    echo "  WARNING: $BIN_DIR/$bin not found — run build_cellswarm.sh first!"
                fi
            fi
        done
    else
        # Workers need cellswarm-worker
        has_bin=$($ADB -s "${ip}:5555" shell "test -x ${BIN_REMOTE}/cellswarm-worker && echo yes" 2>/dev/null | tr -d '\r' || true)
        if [ "$has_bin" != "yes" ]; then
            if [ -f "$BIN_DIR/cellswarm-worker" ]; then
                echo "  Deploying cellswarm-worker to $ip..."
                $ADB -s "${ip}:5555" push "$BIN_DIR/cellswarm-worker" "${BIN_REMOTE}/cellswarm-worker" 2>/dev/null
                $ADB -s "${ip}:5555" shell "chmod +x ${BIN_REMOTE}/cellswarm-worker" 2>/dev/null
                deploy_count=$((deploy_count + 1))
            else
                echo "  WARNING: $BIN_DIR/cellswarm-worker not found — run build_cellswarm.sh first!"
            fi
        fi
    fi
done

if [ "$deploy_count" -eq 0 ]; then
    echo "  All binaries already deployed."
else
    echo "  Deployed $deploy_count binaries."
fi

# ==========================
# Layer weight calculation
# ==========================
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

LW=$(calc_layer_weights "$N_PHONES")

# ==========================
# Dry-run summary
# ==========================
echo ""
echo "============================================"
echo " LAUNCH PLAN"
echo "============================================"
echo "  Phones:      $N_PHONES"
echo "  Master:      ${PHONES[0]} (rank 0)"
echo "  Model:       $(basename "$MODEL_REMOTE")"
if $SPEC_MODE && [ -n "$DRAFT_REMOTE" ]; then
    echo "  Draft:       $(basename "$DRAFT_REMOTE") (max=$DRAFT_MAX)"
else
    echo "  Draft:       none (speculative decoding disabled)"
fi
echo "  Layers:      $TOTAL_LAYERS total → weights: $LW"
echo "  HTTP port:   $HTTP_PORT"
echo "  Context:     $CTX_SIZE"
echo "  Threads:     $THREADS"
echo "  Data port:   $DATA_PORT"
echo "  Signal port: $SIGNAL_PORT"
echo "  Ring order:"
for idx in "${!PHONES[@]}"; do
    role="worker"
    [ "$idx" -eq 0 ] && role="MASTER"
    echo "    rank $idx: ${PHONES[$idx]} ($role)"
done
echo ""

if $DRY_RUN; then
    echo "DRY RUN — not launching. Use without --dry-run to start."
    exit 0
fi

# ==========================
# Helper: adb_shell
# ==========================
adb_shell() {
    local ip=$1; shift
    $ADB -s "${ip}:5555" shell "$@"
}

# ==========================
# Cleanup function
# ==========================
cleanup() {
    echo ""
    echo "Cleaning up all phones..."
    for ip in "${PHONES[@]}"; do
        # Use kill + pidof for reliable cleanup on Android (pkill can miss processes)
        adb_shell "$ip" "
            for name in cellswarm-master cellswarm-worker cellswarm-worker-spec mdns-advertise; do
                pids=\$(pidof \$name 2>/dev/null) && kill -9 \$pids 2>/dev/null
            done
        " 2>/dev/null || true
    done
    # Wait for ports to be fully released (ZMQ SO_LINGER)
    sleep 3
    # Second pass — kill anything that survived
    for ip in "${PHONES[@]}"; do
        adb_shell "$ip" "
            for name in cellswarm-master cellswarm-worker cellswarm-worker-spec; do
                pids=\$(pidof \$name 2>/dev/null) && kill -9 \$pids 2>/dev/null
            done
        " 2>/dev/null || true
    done
    sleep 2
}

trap cleanup EXIT

# ==========================
# Phase 6: Ring formation + launch
# ==========================
echo "[Phase 6] Forming ring and launching..."

# Clean up any existing processes
cleanup 2>/dev/null || true

RANK0_IP="${PHONES[0]}"

# Wait for data port to be free on master phone (ZMQ SO_LINGER can leave zombies)
echo "Checking port $DATA_PORT on master ($RANK0_IP)..."
port_check=$(adb_shell "$RANK0_IP" "ss -tln 2>/dev/null | grep ':${DATA_PORT}\b'" 2>/dev/null | tr -d '\r\n' || true)
if [ -n "$port_check" ]; then
    echo "  Port $DATA_PORT in use — waiting for release..."
    for attempt in $(seq 1 30); do
        sleep 2
        port_check=$(adb_shell "$RANK0_IP" "ss -tln 2>/dev/null | grep ':${DATA_PORT}\b'" 2>/dev/null | tr -d '\r\n' || true)
        if [ -z "$port_check" ]; then
            echo "  Port $DATA_PORT is free."
            break
        fi
        if [ "$attempt" -eq 30 ]; then
            echo "  WARNING: Port $DATA_PORT still in use after 60s — proceeding anyway"
        fi
    done
else
    echo "  Port $DATA_PORT is free."
fi

# Start worker phones (rank 1 to N-1)
echo ""
echo "Starting workers (rank 1-$((N_PHONES-1)))..."
for idx in $(seq 1 $((N_PHONES - 1))); do
    ip="${PHONES[$idx]}"
    next_idx=$(( (idx + 1) % N_PHONES ))
    next_ip="${PHONES[$next_idx]}"

    adb_shell "$ip" "sh -c '
cd /data/local/tmp
taskset $TASKSET ./${BIN_REMOTE#/data/local/tmp/}/cellswarm-worker \
  -m ${MODEL_MAP[$ip]} \
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

DRAFT_FLAGS=""
if $SPEC_MODE && [ -n "$DRAFT_REMOTE" ]; then
    DRAFT_FLAGS="--model-draft $DRAFT_REMOTE --draft-max $DRAFT_MAX"
fi

adb_shell "$RANK0_IP" "sh -c '
cd /data/local/tmp
SWARM_BATCH_PIPELINE=1 taskset $TASKSET ./${BIN_REMOTE#/data/local/tmp/}/cellswarm-master \
  -m $MODEL_REMOTE \
  $DRAFT_FLAGS \
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

# Start mDNS advertisement on phone — cellswarm.local → master phone IP
echo "Starting mDNS: cellswarm.local → $RANK0_IP"
adb_shell "$RANK0_IP" "pkill -9 mdns-advertise" 2>/dev/null || true
adb_shell "$RANK0_IP" "sh -c '
cd /data/local/tmp
./${BIN_REMOTE#/data/local/tmp/}/mdns-advertise cellswarm $RANK0_IP > /data/local/tmp/mdns-advertise.log 2>&1 &
'" 2>/dev/null
sleep 1
mdns_pid=$(adb_shell "$RANK0_IP" "pidof mdns-advertise" 2>/dev/null | tr -d '\r' || true)
if [ -n "$mdns_pid" ]; then
    echo "  mDNS running on phone (pid $mdns_pid)"
else
    echo "  WARNING: mDNS failed to start on phone (dashboard still works via IP)"
fi

# ==========================
# Phase 7: Cross-VLAN mDNS relay
# ==========================
echo ""
echo "[Phase 7] Starting cross-VLAN mDNS relay on host..."

# If mdns-advertise-host exists locally, start it for cross-VLAN resolution
MDNS_HOST_BIN="$BIN_DIR/mdns-advertise-host"
if [ -x "$MDNS_HOST_BIN" ]; then
    pkill -f "mdns-advertise-host" 2>/dev/null || true
    SERVER_IP=$(hostname -I | awk '{print $1}')
    "$MDNS_HOST_BIN" cellswarm "$RANK0_IP" "$SERVER_IP" > /tmp/mdns-advertise-host.log 2>&1 &
    echo "  mDNS relay: cellswarm.local → $RANK0_IP (advertised from $SERVER_IP)"
else
    echo "  mdns-advertise-host not found — skipping cross-VLAN relay"
    echo "  (dashboard accessible via http://${RANK0_IP}:${HTTP_PORT}/)"
fi

echo ""
echo "============================================"
echo " cellswarm is LIVE"
echo "============================================"
echo ""
echo "  Dashboard:  http://cellswarm.local:${HTTP_PORT}/"
echo "              http://${RANK0_IP}:${HTTP_PORT}/"
echo "  API:        http://${RANK0_IP}:${HTTP_PORT}/v1/chat/completions"
echo "  Ring info:  http://${RANK0_IP}:${HTTP_PORT}/api/ring"
echo "  Spec stats: http://${RANK0_IP}:${HTTP_PORT}/api/spec"
echo ""
echo "  Master PID: $master_pid on $RANK0_IP"
echo "  Ring size:  $N_PHONES phones"
echo "  Layers:     $LW ($TOTAL_LAYERS total)"
if $SPEC_MODE && [ -n "$DRAFT_REMOTE" ]; then
    echo "  Draft:      $(basename "$DRAFT_REMOTE") (max=$DRAFT_MAX)"
fi
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
