#!/usr/bin/env bash
# Benchmark prima.cpp phone-only ring — NO host compute.
# All inference runs on phones. Rank 0 = first phone (same prima-worker binary with -r 0).
#
# Usage:
#   ./scripts/bench_prima_phoneonly.sh 3                     # 3-phone ring, Q4_0, CPU only
#   ./scripts/bench_prima_phoneonly.sh 5                     # 5-phone ring (sweet spot)
#   ./scripts/bench_prima_phoneonly.sh 5 --fp16              # 5-phone + FP16 activations (halves transfer)
#   ./scripts/bench_prima_phoneonly.sh 5 --tether rndis      # 5-phone + USB tethering (bypass ADB tunnels)
#   ./scripts/bench_prima_phoneonly.sh 5 --fp16 --tether rndis  # FP16 + tethering (best combo)
#   ./scripts/bench_prima_phoneonly.sh 5 --bottom            # Use bottom phones (avoid other agents)
#   ./scripts/bench_prima_phoneonly.sh 5 --spec              # 5-phone + speculative (rank 0 runs spec binary)
#   ./scripts/bench_prima_phoneonly.sh 5 --spec --draft-max 16  # Tune draft batch size
#   ./scripts/bench_prima_phoneonly.sh 5 --sweep             # Sweep 3,4,5,6 phone configs
#   MODEL=Q4_K_M ./scripts/bench_prima_phoneonly.sh 3        # Use Q4_K_M instead of Q4_0
#
# Prerequisites:
#   - Phones have prima-worker + Q4_0 model deployed (./scripts/deploy_model.sh)
#   - For speculative: prima-worker-spec + draft model on rank 0 (deploy_model.sh --all)

set -euo pipefail

N_PHONES=${1:-5}
SPEC_MODE=false
SWEEP_MODE=false
DRAFT_MAX=8
ACT_QUANT="fp32"
TETHER_MODE=""       # "" = ADB tunnels, "rndis" or "ncm" = USB tethering
USE_BOTTOM=false     # Use bottom phones from device list

# Parse flags
shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        --spec)      SPEC_MODE=true ;;
        --sweep)     SWEEP_MODE=true ;;
        --draft-max) shift; DRAFT_MAX="$1" ;;
        --fp16)      ACT_QUANT="fp16" ;;
        --fp32)      ACT_QUANT="fp32" ;;
        --tether)    TETHER_MODE="${2:-rndis}"; shift ;;
        --tether-ncm) TETHER_MODE="ncm" ;;
        --bottom)    USE_BOTTOM=true ;;
        *)           echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ADB='"C:\Program Files\platform-tools\adb.exe"'
SSH_HOST="winpc"

# Model selection (Q4_0 by default, override with MODEL=Q4_K_M)
MODEL_QUANT="${MODEL:-Q4_0}"
MODEL_REMOTE="/data/local/tmp/adb-llm/models/deepseek-coder-33b-instruct.${MODEL_QUANT}.gguf"
DRAFT_REMOTE="/data/local/tmp/adb-llm/models/deepseek-coder-1.3b-instruct.Q4_0.gguf"

# Phone serial numbers (all 20 Samsung Galaxy Z Fold3)
ALL_PHONES_TOP=(R3CR904AQKA R3CR90AJCPF R3CRA0KPK9M R3CRB0726FZ
                R3CRC0697RY R3CRC071RZH R3CRC085GGE R3CRC09292M
                R3CRC0MVVDN R3CT40EHA2B R3CT40TJY8T R3CT505TXMW
                R3CT508XJRF R3CT60BAJXK RFCR70TXFXJ RFCR71N4AFH
                RFCR80H5CRZ RFCRA19TM7Y RFCRB0B1N6Y RFCRB0BYFAX)

# Bottom phones (reverse order) — use with --bottom to avoid conflicts
ALL_PHONES_BOTTOM=(RFCRB0BYFAX RFCRB0B1N6Y RFCRA19TM7Y RFCR80H5CRZ
                   RFCR71N4AFH RFCR70TXFXJ R3CT60BAJXK R3CT508XJRF
                   R3CT505TXMW R3CT40TJY8T R3CT40EHA2B R3CRC0MVVDN
                   R3CRC09292M R3CRC085GGE R3CRC071RZH R3CRC0697RY
                   R3CRB0726FZ R3CRA0KPK9M R3CR90AJCPF R3CR904AQKA)

if $USE_BOTTOM; then
    ALL_PHONES=("${ALL_PHONES_BOTTOM[@]}")
else
    ALL_PHONES=("${ALL_PHONES_TOP[@]}")
fi

TOTAL_LAYERS=62  # DeepSeek Coder 33B

# Ports — phone-only ring uses same port scheme but ALL on phones
DATA_PORT=9000
SIGNAL_PORT=10000
FWD_BASE=59001     # WinPC intermediate ports for ADB forward (inbound)
REV_BASE=58001     # WinPC intermediate ports for ADB reverse (outbound)
SIG_OFFSET=100

# =========================================
# Functions
# =========================================

cleanup() {
    echo "Cleaning up..."
    # SIGKILL (-9) to ensure zombie processes are killed
    for SERIAL in "${ALL_PHONES[@]}"; do
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell pkill -9 prima-worker" 2>/dev/null || true
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell pkill -9 prima-worker-spec" 2>/dev/null || true
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL forward --remove-all" 2>/dev/null || true
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL reverse --remove-all" 2>/dev/null || true
    done
    # Kill SSH tunnels on data/signal ports and REV_BASE range
    for port in $(seq $DATA_PORT $((DATA_PORT + 20))) $(seq $SIGNAL_PORT $((SIGNAL_PORT + 20))); do
        fuser -k "${port}/tcp" 2>/dev/null || true
    done
    pkill -f prima-worker 2>/dev/null || true
    rm -f /tmp/ssh-prima-* 2>/dev/null
    echo "Cleanup done."
}

# Calculate layer distribution across N phones
# In spec mode, rank 0 gets fewer layers (needs RAM for draft model ~850MB)
# Returns layer weight string like "13,13,12,12,12"
calc_layer_weights() {
    local n_phones=$1
    local spec=${2:-false}

    if [ "$spec" = "true" ]; then
        # Rank 0 needs ~850MB extra for draft model.
        # Give rank 0 ~6 fewer layers to free up ~1.8GB.
        # Target layer budget: 303MB/layer → 6 fewer layers saves ~1.8GB
        local rank0_reduction=6
        local rank0_layers=$(( (TOTAL_LAYERS / n_phones) - rank0_reduction ))
        if [ "$rank0_layers" -lt 5 ]; then
            rank0_layers=5
        fi
        local remaining=$((TOTAL_LAYERS - rank0_layers))
        local other_phones=$((n_phones - 1))
        local per_other=$((remaining / other_phones))
        local other_remainder=$((remaining % other_phones))
        local lw="$rank0_layers"
        for i in $(seq 1 $((other_phones))); do
            local extra=0
            if [ "$((i - 1))" -lt "$other_remainder" ]; then
                extra=1
            fi
            lw="${lw},$((per_other + extra))"
        done
        echo "$lw"
    else
        local layers_per_phone=$((TOTAL_LAYERS / n_phones))
        local remainder=$((TOTAL_LAYERS % n_phones))
        local lw=""
        for i in $(seq 0 $((n_phones - 1))); do
            local extra=0
            if [ "$i" -lt "$remainder" ]; then
                extra=1
            fi
            if [ -n "$lw" ]; then
                lw="${lw},$((layers_per_phone + extra))"
            else
                lw="$((layers_per_phone + extra))"
            fi
        done
        echo "$lw"
    fi
}

# Set up phone-to-phone tunnels through WinPC
# In a phone-only ring, ALL connections go through WinPC (ADB forward/reverse).
# No SSH tunnels to the coding server needed.
#
# Topology for N phones (ring: 0→1→2→...→N-1→0):
#   Each phone rank R:
#     - BINDS (listens) on: data_port+R, signal_port+R
#     - CONNECTS (sends) to localhost:(data_port + next_rank)
#
#   Tunneling via WinPC for each pair (sender → receiver):
#     Sender:  ADB reverse tcp:(data_port+receiver_rank) tcp:WIN_FWD_PORT
#     WinPC:   ADB forward tcp:WIN_FWD_PORT on receiver → phone:(data_port+receiver_rank)
#     (Both sides see localhost:PORT, WinPC bridges them)

setup_phone_ring_tunnels() {
    local n_phones=$1
    local phones=("${@:2}")

    echo "Setting up phone ring tunnels via SSH relay (${n_phones} phones)..."
    echo "  Pattern: phone → ADB reverse → WinPC → SSH → coding_server → SSH → WinPC → ADB forward → phone"

    # ADB reverse→ADB forward (direct) doesn't work because the ADB daemon on WinPC
    # can't route connections between its own forward and reverse listeners.
    # Solution: use SSH tunnels through the coding server as a TCP relay.
    #
    # For each phone (rank R):
    #   INBOUND (others reaching this phone's bind port):
    #     ADB forward: WinPC:FWD_PORT → phone:(data_port+R)
    #     SSH -L: coding_server:(data_port+R) → WinPC:FWD_PORT
    #
    #   OUTBOUND (this phone reaching next rank's bind port):
    #     ADB reverse: phone:(data_port+next_rank) → WinPC:REV_PORT
    #     SSH -R: WinPC:REV_PORT → coding_server:(data_port+next_rank)
    #     (coding_server has SSH -L listener on that port, which routes to next phone)

    # Step 1: INBOUND — ADB forward + SSH -L for each phone's bind ports
    for idx in $(seq 0 $((n_phones - 1))); do
        local serial="${phones[$idx]}"
        local rank=$idx

        local phone_data_bind=$((DATA_PORT + rank))
        local phone_signal_bind=$((SIGNAL_PORT + rank))

        local win_fwd_data=$((FWD_BASE + idx))
        local win_fwd_sig=$((FWD_BASE + SIG_OFFSET + idx))

        # ADB forward: WinPC:FWD → phone:BIND
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial forward tcp:$win_fwd_data tcp:$phone_data_bind"
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial forward tcp:$win_fwd_sig tcp:$phone_signal_bind"

        # SSH -L: coding_server:BIND → WinPC:FWD → phone:BIND
        ssh -o ConnectTimeout=10 -o Compression=no -N \
            -L "$phone_data_bind:127.0.0.1:$win_fwd_data" \
            -L "$phone_signal_bind:127.0.0.1:$win_fwd_sig" \
            "$SSH_HOST" -o ServerAliveInterval=10 &

        echo "  [$serial] rank=$rank INBOUND: phone:$phone_data_bind ← WinPC:$win_fwd_data ← coding_server:$phone_data_bind"
    done

    sleep 1

    # Step 2: OUTBOUND — ADB reverse + SSH -R for each phone's send path
    for idx in $(seq 0 $((n_phones - 1))); do
        local serial="${phones[$idx]}"
        local rank=$idx
        local next_rank=$(( (rank + 1) % n_phones ))

        # Phone connects to localhost:(data_port + next_rank)
        local phone_connect_data=$((DATA_PORT + next_rank))
        local phone_connect_sig=$((SIGNAL_PORT + next_rank))

        # Unique WinPC reverse ports for this connection
        local win_rev_data=$((REV_BASE + idx))
        local win_rev_sig=$((REV_BASE + SIG_OFFSET + idx))

        # SSH -R: WinPC:REV → coding_server:(data_port+next_rank)
        # The coding_server has SSH -L on (data_port+next_rank) from Step 1
        ssh -o ConnectTimeout=10 -o Compression=no -N \
            -R "$win_rev_data:127.0.0.1:$phone_connect_data" \
            -R "$win_rev_sig:127.0.0.1:$phone_connect_sig" \
            "$SSH_HOST" -o ServerAliveInterval=10 &
        sleep 0.5

        # ADB reverse: phone:CONNECT → WinPC:REV
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial reverse tcp:$phone_connect_data tcp:$win_rev_data"
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial reverse tcp:$phone_connect_sig tcp:$win_rev_sig"

        echo "  [$serial] rank=$rank OUTBOUND: phone:$phone_connect_data → WinPC:$win_rev_data → coding_server:$phone_connect_data"
    done

    # Also set up master_socket tunnel for middle ranks (connect to rank 0 directly)
    # Middle ranks have a master_socket that connects to data_port+0 = DATA_PORT
    if true; then
    local master_data=$((DATA_PORT + 0))
    local master_sig=$((SIGNAL_PORT + 0))
    for idx in $(seq 1 $((n_phones - 2))); do
        local serial="${phones[$idx]}"
        local rank=$idx

        # Skip if this rank's next hop is already rank 0 (last rank)
        local next_rank=$(( (rank + 1) % n_phones ))
        if [ "$next_rank" -eq 0 ]; then
            continue  # Already has ADB reverse for port 9000
        fi

        # Middle rank needs to reach rank 0 via master_socket
        # Use a unique REV port for this master connection
        local win_rev_master_data=$((REV_BASE + 50 + idx))
        local win_rev_master_sig=$((REV_BASE + SIG_OFFSET + 50 + idx))

        ssh -o ConnectTimeout=10 -o Compression=no -N \
            -R "$win_rev_master_data:127.0.0.1:$master_data" \
            -R "$win_rev_master_sig:127.0.0.1:$master_sig" \
            "$SSH_HOST" -o ServerAliveInterval=10 &
        sleep 0.3

        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial reverse tcp:$master_data tcp:$win_rev_master_data"
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial reverse tcp:$master_sig tcp:$win_rev_master_sig"

        echo "  [$serial] rank=$rank MASTER: phone:$master_data → WinPC:$win_rev_master_data → coding_server:$master_data"
    done
    fi  # end of if false (master tunnels disabled)

    echo "  Ring tunnels established (via SSH relay)."
}

# Verify tunnel topology is correct
verify_tunnels() {
    local n_phones=$1
    local phones=("${@:2}")

    echo ""
    echo "=== TUNNEL VERIFICATION ==="

    # 1. Check SSH -L ports on coding server
    echo "  [coding server] Listening ports (data):"
    for rank in $(seq 0 $((n_phones - 1))); do
        local port=$((DATA_PORT + rank))
        if ss -tlnp 2>/dev/null | grep -q ":${port} " 2>/dev/null; then
            echo "    port $port: LISTENING ✓"
        else
            echo "    port $port: NOT LISTENING ✗"
        fi
    done

    # 2. ADB reverse/forward rules on each phone
    echo "  [phones] ADB rules:"
    for idx in $(seq 0 $((n_phones - 1))); do
        local serial="${phones[$idx]}"
        echo "    Phone $idx ($serial):"
        echo "      Forward:"
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $serial forward --list" 2>/dev/null | while read -r line; do
            echo "        $line"
        done
        echo "      Reverse:"
        ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $serial reverse --list" 2>/dev/null | while read -r line; do
            echo "        $line"
        done
    done

    # 3. TCP connectivity test through the full tunnel chain
    # For each send→recv pair, try connecting from coding server through SSH -L
    echo "  [connectivity] TCP port reachability from coding server:"
    for rank in $(seq 0 $((n_phones - 1))); do
        local port=$((DATA_PORT + rank))
        # Try connecting to the coding server's SSH -L port (should reach through to phone)
        # Use timeout + nc to test. nc -z just tests if port accepts connections.
        if timeout 3 bash -c "echo | nc -w 2 127.0.0.1 $port" >/dev/null 2>&1; then
            echo "    cs:$port → phone${rank}:$port: REACHABLE ✓"
        else
            echo "    cs:$port → phone${rank}:$port: UNREACHABLE ✗"
        fi
    done

    echo "=== END VERIFICATION ==="
    echo ""
}

# Start prima-workers on all phones
# Rank 0 phone handles token embedding/output + its share of layers
start_phone_workers() {
    local n_phones=$1
    local lw=$2
    local spec=$3
    local act_q=$4
    local tether=$5
    local phones=("${@:6}")

    echo "Starting prima-workers on $n_phones phones (act_quant=$act_q, tether=$tether)..."

    # Build activation quantization flag
    local act_flag=""
    if [ "$act_q" = "fp16" ]; then
        act_flag="--act-quant fp16"
    fi

    for idx in $(seq 0 $((n_phones - 1))); do
        local serial="${phones[$idx]}"
        local rank=$idx

        local script_local="/tmp/prima-phoneonly-rank${rank}.sh"
        local script_win="C:\\Users\\Lukio-4090\\prima-phoneonly-rank${rank}.sh"
        local script_phone="/data/local/tmp/prima-start.sh"

        # Determine master and next IPs
        local master_ip="127.0.0.1"
        local next_ip="127.0.0.1"
        if [ -n "$tether" ]; then
            # USB tethering: use direct IPs (10.0.0.X)
            local next_rank=$(( (rank + 1) % n_phones ))
            master_ip="10.0.0.1"
            next_ip="10.0.0.$((next_rank + 1))"
        fi

        # Rank 0 with speculative mode uses prima-worker-spec
        if [ "$rank" -eq 0 ] && [ "$spec" = "true" ]; then
            cat > "$script_local" << ENDSCRIPT
#!/system/bin/sh
cd /data/local/tmp
taskset f0 ./adb-llm/bin/prima-worker-spec \
  -m $MODEL_REMOTE \
  --model-draft $DRAFT_REMOTE \
  --world $n_phones --rank 0 \
  --master $master_ip --next $next_ip \
  --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
  -lw $lw -c 512 -t 4 \
  --no-mmap --prefetch $act_flag \
  --draft-max $DRAFT_MAX \
  -p "Write a Python function that computes the Fibonacci sequence efficiently using dynamic programming." \
  -n 64 \
  > /data/local/tmp/prima-worker.log 2>&1 &
ENDSCRIPT
        elif [ "$rank" -eq 0 ]; then
            # Rank 0 non-speculative: also produces output (runs to completion)
            cat > "$script_local" << ENDSCRIPT
#!/system/bin/sh
cd /data/local/tmp
taskset f0 ./adb-llm/bin/prima-worker \
  -m $MODEL_REMOTE \
  --world $n_phones --rank 0 \
  --master $master_ip --next $next_ip \
  --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
  -lw $lw -c 512 -t 4 \
  --no-mmap --prefetch $act_flag \
  -p "Write a Python function that computes the Fibonacci sequence efficiently using dynamic programming." \
  -n 64 \
  > /data/local/tmp/prima-worker.log 2>&1 &
ENDSCRIPT
        else
            # Non-rank-0: wait forever for tokens (-n -1)
            cat > "$script_local" << ENDSCRIPT
#!/system/bin/sh
cd /data/local/tmp
taskset f0 ./adb-llm/bin/prima-worker \
  -m $MODEL_REMOTE \
  --world $n_phones --rank $rank \
  --master $master_ip --next $next_ip \
  --data-port $DATA_PORT --signal-port $SIGNAL_PORT \
  -lw $lw -c 512 -n -1 -t 4 \
  --no-mmap --prefetch $act_flag \
  > /data/local/tmp/prima-worker.log 2>&1 &
ENDSCRIPT
        fi

        scp -o ConnectTimeout=10 "$script_local" "${SSH_HOST}:${script_win}" >/dev/null 2>&1
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial push \"$script_win\" $script_phone" >/dev/null 2>&1
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial shell chmod 755 $script_phone" 2>/dev/null

        # Start workers in reverse order: non-rank-0 first, then rank 0 last
        # (rank 0 drives the generation, others must be ready first)
        if [ "$rank" -gt 0 ]; then
            ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial shell sh $script_phone" 2>/dev/null
            echo "  Started rank $rank on $serial"
        fi
    done

    echo "Waiting for non-rank-0 workers to initialize (120s for --no-mmap model load)..."
    sleep 120

    # Verify non-rank-0 workers
    for idx in $(seq 1 $((n_phones - 1))); do
        local serial="${phones[$idx]}"
        local pid
        pid=$(ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial shell pidof prima-worker" 2>/dev/null || true)
        if [ -n "$pid" ]; then
            echo "  $serial (rank $idx): prima-worker running (pid $pid)"
        else
            echo "  $serial (rank $idx): WARNING — prima-worker NOT running!"
            ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial shell tail -20 /data/local/tmp/prima-worker.log" 2>/dev/null || true
        fi
    done

    # Now start rank 0 (drives generation)
    local rank0_serial="${phones[0]}"
    echo ""
    echo "Starting rank 0 (generation driver) on $rank0_serial..."
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $rank0_serial shell sh /data/local/tmp/prima-start.sh" 2>/dev/null
    echo "  Rank 0 started. Waiting for inference to complete..."
}

# Wait for rank 0 to finish and extract results
wait_and_report() {
    local rank0_serial=$1
    local n_phones=$2
    local lw=$3
    local log_file=$4

    echo ""
    echo "Monitoring rank 0 output..."

    # Poll rank 0's log for completion (look for "llama_perf" or process exit)
    local max_wait=600  # 10 minutes max
    local elapsed=0
    local done=false

    while [ "$elapsed" -lt "$max_wait" ]; do
        sleep 5
        elapsed=$((elapsed + 5))

        # Check if process is still running
        local pid
        pid=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $rank0_serial shell pidof prima-worker" 2>/dev/null || true)
        # Also check for speculative binary
        if [ -z "$pid" ]; then
            pid=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $rank0_serial shell pidof prima-worker-spec" 2>/dev/null || true)
        fi

        if [ -z "$pid" ]; then
            echo "  Rank 0 process finished (${elapsed}s elapsed)."
            done=true
            break
        fi

        # Check for completion markers in log
        local has_perf
        has_perf=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $rank0_serial shell grep -c llama_perf /data/local/tmp/prima-worker.log 2>/dev/null" || echo "0")
        if [ "${has_perf:-0}" -gt 0 ]; then
            echo "  Inference complete (${elapsed}s elapsed)."
            done=true
            break
        fi

        echo "  Still running... (${elapsed}s elapsed)"
    done

    if ! $done; then
        echo "  TIMEOUT after ${max_wait}s!"
    fi

    # Fetch the full log from rank 0
    echo ""
    echo "--- Rank 0 Log ---"
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $rank0_serial shell cat /data/local/tmp/prima-worker.log" 2>/dev/null | tee "$log_file"

    # Fetch logs from all other ranks
    local phones_arr=("${ALL_PHONES[@]:0:$n_phones}")
    for idx in $(seq 1 $((n_phones - 1))); do
        local serial="${phones_arr[$idx]}"
        echo ""
        echo "--- Rank $idx Log ($serial) [last 40 lines] ---"
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $serial shell tail -40 /data/local/tmp/prima-worker.log" 2>/dev/null || echo "  (no log)"
    done

    echo ""
    echo "--- Key Metrics ---"
    grep -E "(speed:|tok/s|n_draft|n_predict|n_drafted|n_accept|accept|eval time|sample time|total time)" "$log_file" 2>/dev/null || echo "  No metrics found in log."

    echo ""
    echo "Log saved: $log_file"
}

# Run a single benchmark with N phones
run_benchmark() {
    local n_phones=$1
    local spec=$2
    local phones=("${ALL_PHONES[@]:0:$n_phones}")
    local lw
    lw=$(calc_layer_weights "$n_phones" "$spec")

    local mode_str="phone-only"
    if [ "$spec" = "true" ]; then
        mode_str="$mode_str + speculative (draft-max=$DRAFT_MAX)"
    fi
    if [ "$ACT_QUANT" = "fp16" ]; then
        mode_str="$mode_str + FP16 activations"
    fi
    if [ -n "$TETHER_MODE" ]; then
        mode_str="$mode_str + USB tethering ($TETHER_MODE)"
    fi

    echo ""
    echo "============================================"
    echo " ${n_phones}-PHONE RING — $mode_str"
    echo "============================================"
    echo "  Model:    $(basename "$MODEL_REMOTE") ($MODEL_QUANT)"
    echo "  Phones:   ${phones[*]}"
    echo "  Layers:   $lw"
    echo "  Act quant: $ACT_QUANT"
    if [ -n "$TETHER_MODE" ]; then
        echo "  Transport: USB tethering ($TETHER_MODE)"
    else
        echo "  Transport: ADB tunnels (SSH relay)"
    fi
    if [ "$spec" = "true" ]; then
        echo "  Draft:    $(basename "$DRAFT_REMOTE")"
        echo "  Draft max: $DRAFT_MAX tokens"
    fi
    echo ""

    cleanup 2>/dev/null || true
    sleep 1

    if [ -n "$TETHER_MODE" ]; then
        # USB tethering: no tunnel setup needed, phones communicate directly
        echo "Using USB tethering transport (phones must have tethering enabled)."
        echo "Run ./scripts/setup_usb_tethering.sh first if not already done."
    else
        setup_phone_ring_tunnels "$n_phones" "${phones[@]}"
    fi
    sleep 3

    # Verify tunnel topology before starting workers
    if [ -z "$TETHER_MODE" ]; then
        verify_tunnels "$n_phones" "${phones[@]}"
    fi

    local log_file="/tmp/prima-phoneonly-${n_phones}phone"
    if [ "$ACT_QUANT" = "fp16" ]; then
        log_file="${log_file}-fp16"
    fi
    if [ -n "$TETHER_MODE" ]; then
        log_file="${log_file}-tether"
    fi
    if [ "$spec" = "true" ]; then
        log_file="${log_file}-spec-d${DRAFT_MAX}"
    fi
    log_file="${log_file}-${MODEL_QUANT}.log"

    start_phone_workers "$n_phones" "$lw" "$spec" "$ACT_QUANT" "$TETHER_MODE" "${phones[@]}"
    wait_and_report "${phones[0]}" "$n_phones" "$lw" "$log_file"

    cleanup 2>/dev/null || true
}

# =========================================
# Main
# =========================================

trap cleanup EXIT

if $SWEEP_MODE; then
    echo "============================================"
    echo " PHONE-ONLY SWEEP — testing 3, 4, 5, 6 phones"
    echo "============================================"
    echo ""

    for n in 3 4 5 6; do
        run_benchmark "$n" "false"
        echo ""
        echo "============================================"
        echo ""
        sleep 5
    done

    echo ""
    echo "============================================"
    echo " SWEEP COMPLETE — check logs:"
    for n in 3 4 5 6; do
        echo "  /tmp/prima-phoneonly-${n}phone-${MODEL_QUANT}.log"
    done
    echo "============================================"
else
    run_benchmark "$N_PHONES" "$SPEC_MODE"
fi
