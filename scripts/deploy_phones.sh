#!/usr/bin/env bash
# Deploy cellswarm-worker binary and/or model to N phones via ADB through WinPC.
#
# Usage:
#   ./scripts/deploy_phones.sh               # Deploy binary+model to first 4 phones
#   ./scripts/deploy_phones.sh 8             # Deploy to first 8 phones
#   ./scripts/deploy_phones.sh 8 --binary    # Binary only (skip model push)
#   ./scripts/deploy_phones.sh 8 --model     # Model only (skip binary push)
#   ./scripts/deploy_phones.sh 20 --status   # Just check status of 20 phones
#
# All ADB commands go through SSH to WinPC (10.69.1.114) since phones are USB-connected there.

set -euo pipefail

N_PHONES=${1:-4}
MODE=${2:---all}  # --all, --binary, --model, --status

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SWARM_WORKER="$PROJECT_DIR/bin/cellswarm-worker"
ADB='"C:\Program Files\platform-tools\adb.exe"'
SSH_HOST="winpc"

REMOTE_BASE="/data/local/tmp/cellswarm"
REMOTE_BIN="$REMOTE_BASE/bin"
REMOTE_MODELS="$REMOTE_BASE/models"

# Local model to push
MODEL_LOCAL="$HOME/models/deepseek-coder-33b-instruct.Q4_K_M.gguf"
MODEL_REMOTE="$REMOTE_MODELS/deepseek-coder-33b-instruct.Q4_K_M.gguf"
# Windows staging path for SCP -> ADB push
WIN_STAGING="C:\\Users\\Lukio-4090\\cellswarm-deploy"

ALL_PHONES=(R3CR904AQKA R3CR90AJCPF R3CRA0KPK9M R3CRB0726FZ
            R3CRA0CJCDD R3CRA0CP7TZ R3CRA0F51KB R3CRA0E77WZ
            R3CR904BTTM R3CRA0CZ35E R3CRA0D4LJZ R3CRA0D7E6Z
            R3CRA0D98AZ R3CRA0DQKVZ R3CRA0EK7HM R3CRA0FR3VD
            R3CRA0GJ4SF R3CRA0GJK0E R3CRA0GJK5M R3CRA0GK04F)

if [ "$N_PHONES" -gt "${#ALL_PHONES[@]}" ]; then
    echo "ERROR: Requested $N_PHONES phones but only ${#ALL_PHONES[@]} available"
    exit 1
fi

PHONES=("${ALL_PHONES[@]:0:$N_PHONES}")

echo "============================================"
echo " cellswarm phone deployment — $N_PHONES devices"
echo "============================================"
echo "  Mode: $MODE"
echo "  Phones: ${PHONES[*]}"
echo ""

# --- Status check ---
if [ "$MODE" = "--status" ]; then
    echo "Checking device status..."
    for SERIAL in "${PHONES[@]}"; do
        echo -n "  $SERIAL: "

        # Check device online
        ONLINE=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL get-state" 2>/dev/null || echo "offline")
        if [ "$ONLINE" != "device" ]; then
            echo "OFFLINE ($ONLINE)"
            continue
        fi

        # Check binary
        BIN_OK=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell ls -l $REMOTE_BIN/cellswarm-worker 2>/dev/null" || echo "missing")
        # Check model
        MODEL_OK=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell ls -l $MODEL_REMOTE 2>/dev/null" || echo "missing")
        # Check RAM
        RAM=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell cat /proc/meminfo 2>/dev/null" | head -1 | awk '{print $2}')
        RAM_GB=$(echo "scale=1; ${RAM:-0}/1048576" | bc 2>/dev/null || echo "?")

        if echo "$BIN_OK" | grep -q "cellswarm-worker"; then
            BIN_STATUS="binary OK"
        else
            BIN_STATUS="NO binary"
        fi

        if echo "$MODEL_OK" | grep -q ".gguf"; then
            MODEL_STATUS="model OK"
        else
            MODEL_STATUS="NO model"
        fi

        echo "online, ${RAM_GB}GB RAM, $BIN_STATUS, $MODEL_STATUS"
    done
    exit 0
fi

# --- Validate local files ---
if [ "$MODE" = "--all" ] || [ "$MODE" = "--binary" ]; then
    if [ ! -f "$SWARM_WORKER" ]; then
        echo "ERROR: $SWARM_WORKER not found. Run ./scripts/build_cellswarm.sh first."
        exit 1
    fi
    echo "Binary: $SWARM_WORKER ($(stat -c%s "$SWARM_WORKER" | numfmt --to=iec))"
fi

if [ "$MODE" = "--all" ] || [ "$MODE" = "--model" ]; then
    if [ ! -f "$MODEL_LOCAL" ]; then
        echo "ERROR: $MODEL_LOCAL not found."
        exit 1
    fi
    echo "Model:  $MODEL_LOCAL ($(stat -c%s "$MODEL_LOCAL" | numfmt --to=iec))"
fi

# --- Stage files to WinPC ---
echo ""
echo "Staging files to WinPC..."
ssh -o ConnectTimeout=10 "$SSH_HOST" "mkdir -p \"$WIN_STAGING\"" 2>/dev/null || true

if [ "$MODE" = "--all" ] || [ "$MODE" = "--binary" ]; then
    echo "  Uploading cellswarm-worker to WinPC..."
    scp -o ConnectTimeout=30 "$SWARM_WORKER" "${SSH_HOST}:${WIN_STAGING}\\cellswarm-worker"
    echo "  Binary staged."
fi

if [ "$MODE" = "--all" ] || [ "$MODE" = "--model" ]; then
    # Check if model already exists on WinPC to avoid re-uploading 19GB
    WIN_MODEL_SIZE=$(ssh -o ConnectTimeout=10 "$SSH_HOST" "wc -c < \"${WIN_STAGING}\\deepseek-coder-33b-instruct.Q4_K_M.gguf\" 2>/dev/null" || echo "0")
    LOCAL_MODEL_SIZE=$(stat -c%s "$MODEL_LOCAL")
    if [ "${WIN_MODEL_SIZE:-0}" = "$LOCAL_MODEL_SIZE" ]; then
        echo "  Model already staged on WinPC (size matches), skipping upload."
    else
        echo "  Uploading model to WinPC (this may take a while for 19GB)..."
        scp -o ConnectTimeout=30 "$MODEL_LOCAL" "${SSH_HOST}:${WIN_STAGING}\\deepseek-coder-33b-instruct.Q4_K_M.gguf"
        echo "  Model staged."
    fi
fi

# --- Deploy to each phone ---
echo ""
SUCCESS=0
FAIL=0

for SERIAL in "${PHONES[@]}"; do
    echo "--- Deploying to $SERIAL ---"

    # Check device online
    ONLINE=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL get-state" 2>/dev/null || echo "offline")
    if [ "$ONLINE" != "device" ]; then
        echo "  SKIPPED — device offline ($ONLINE)"
        FAIL=$((FAIL + 1))
        continue
    fi

    # Create directories
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell mkdir -p $REMOTE_BIN $REMOTE_MODELS" 2>/dev/null

    if [ "$MODE" = "--all" ] || [ "$MODE" = "--binary" ]; then
        echo "  Pushing cellswarm-worker..."
        ssh -o ConnectTimeout=30 "$SSH_HOST" "$ADB -s $SERIAL push \"${WIN_STAGING}\\cellswarm-worker\" $REMOTE_BIN/cellswarm-worker" 2>&1 | tail -1
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell chmod 755 $REMOTE_BIN/cellswarm-worker" 2>/dev/null
    fi

    if [ "$MODE" = "--all" ] || [ "$MODE" = "--model" ]; then
        # Check if model already exists on phone (skip 19GB push if so)
        PHONE_MODEL_SIZE=$(ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell wc -c < $MODEL_REMOTE 2>/dev/null" || echo "0")
        LOCAL_MODEL_SIZE=$(stat -c%s "$MODEL_LOCAL")
        if [ "${PHONE_MODEL_SIZE:-0}" = "$LOCAL_MODEL_SIZE" ]; then
            echo "  Model already on phone (size matches), skipping."
        else
            echo "  Pushing model (~19GB, this will take several minutes)..."
            ssh -o ConnectTimeout=600 "$SSH_HOST" "$ADB -s $SERIAL push \"${WIN_STAGING}\\deepseek-coder-33b-instruct.Q4_K_M.gguf\" $MODEL_REMOTE" 2>&1 | tail -1
        fi
    fi

    echo "  Done."
    SUCCESS=$((SUCCESS + 1))
done

echo ""
echo "============================================"
echo " DEPLOYMENT COMPLETE"
echo "============================================"
echo "  Success: $SUCCESS / $N_PHONES"
if [ "$FAIL" -gt 0 ]; then
    echo "  Failed:  $FAIL"
fi
echo ""
echo "Verify with: ./scripts/deploy_phones.sh $N_PHONES --status"
echo "============================================"
