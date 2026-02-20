#!/usr/bin/env bash
# Deploy Q4_0 model (and optionally Q4_0 draft model) to N phones via ADB through WinPC.
#
# Usage:
#   ./scripts/deploy_model.sh                       # Deploy Q4_0 target model to first 5 phones
#   ./scripts/deploy_model.sh 8                     # Deploy to first 8 phones
#   ./scripts/deploy_model.sh 5 --draft             # Deploy draft model only (rank 0 phone)
#   ./scripts/deploy_model.sh 5 --both              # Deploy both target + draft models
#   ./scripts/deploy_model.sh 5 --spec-binary       # Deploy cellswarm-worker-spec binary to rank 0
#   ./scripts/deploy_model.sh 5 --all               # Deploy target model, draft model, and spec binary
#   ./scripts/deploy_model.sh 5 --status            # Check Q4_0 model status on phones
#   MODEL_Q4_0=~/models/other.Q4_0.gguf ./scripts/deploy_model.sh 5
#
# Deploys:
#   - Target: deepseek-coder-33b-instruct.Q4_0.gguf (~17GB) to ALL phones
#   - Draft:  deepseek-coder-1.3b-instruct.Q4_0.gguf (~850MB) to rank 0 phone only
#   - Binary: cellswarm-worker-spec (ARM64 speculative binary) to rank 0 phone only
#
# If Q4_0 model is not found locally, offers to quantize from Q4_K_M using llama-quantize.

set -euo pipefail

N_PHONES=${1:-5}
MODE=${2:---target}  # --target, --draft, --both, --spec-binary, --all, --status

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ADB='"C:\Program Files\platform-tools\adb.exe"'
SSH_HOST="winpc"

REMOTE_BASE="/data/local/tmp/cellswarm"
REMOTE_BIN="$REMOTE_BASE/bin"
REMOTE_MODELS="$REMOTE_BASE/models"

# Models
MODEL_Q4_0="${MODEL_Q4_0:-$HOME/models/deepseek-coder-33b-instruct.Q4_0.gguf}"
MODEL_Q4_K_M="$HOME/models/deepseek-coder-33b-instruct.Q4_K_M.gguf"
DRAFT_Q4_0="${DRAFT_Q4_0:-$HOME/models/deepseek-coder-1.3b-instruct.Q4_0.gguf}"
DRAFT_Q4_K_M="$HOME/models/deepseek-coder-1.3b-instruct.Q4_K_M.gguf"
SPEC_BINARY="$PROJECT_DIR/bin/cellswarm-worker-spec"

# Remote paths
REMOTE_MODEL_Q4_0="$REMOTE_MODELS/deepseek-coder-33b-instruct.Q4_0.gguf"
REMOTE_DRAFT_Q4_0="$REMOTE_MODELS/deepseek-coder-1.3b-instruct.Q4_0.gguf"
REMOTE_SPEC_BIN="$REMOTE_BIN/cellswarm-worker-spec"

# Windows staging
WIN_STAGING="C:\\Users\\Lukio-4090\\cellswarm-deploy-q40"

# llama-quantize (host x86_64 build — uses vendor cellswarm)
LLAMA_QUANTIZE="$HOME/llama.cpp/build/bin/llama-quantize"

# Phone list (all 20 Samsung Galaxy Z Fold3)
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
echo " Q4_0 model deployment — $N_PHONES devices"
echo "============================================"
echo "  Mode: $MODE"
echo "  Phones: ${PHONES[*]}"
echo ""

# --- Status check ---
if [ "$MODE" = "--status" ]; then
    echo "Checking Q4_0 model status on phones..."
    for idx in "${!PHONES[@]}"; do
        SERIAL="${PHONES[$idx]}"
        echo -n "  $SERIAL (rank $((idx+1))): "

        ONLINE=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL get-state" 2>/dev/null | tr -d '\r' || echo "offline")
        if [ "$ONLINE" != "device" ]; then
            echo "OFFLINE ($ONLINE)"
            continue
        fi

        # Check target model
        TARGET_SIZE=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell wc -c < $REMOTE_MODEL_Q4_0 2>/dev/null" | tr -d '\r' || echo "0")
        if [ "${TARGET_SIZE:-0}" -gt 1000000000 ]; then
            TARGET_GB=$(echo "scale=1; ${TARGET_SIZE}/1073741824" | bc 2>/dev/null || echo "?")
            TARGET_STATUS="Q4_0 target OK (${TARGET_GB}GB)"
        else
            TARGET_STATUS="NO Q4_0 target"
        fi

        # Check draft model (only on rank 0)
        DRAFT_STATUS=""
        if [ "$idx" -eq 0 ]; then
            DRAFT_SIZE=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell wc -c < $REMOTE_DRAFT_Q4_0 2>/dev/null" | tr -d '\r' || echo "0")
            if [ "${DRAFT_SIZE:-0}" -gt 100000000 ]; then
                DRAFT_STATUS=", draft OK"
            else
                DRAFT_STATUS=", NO draft"
            fi

            # Check spec binary
            SPEC_OK=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell ls -l $REMOTE_SPEC_BIN 2>/dev/null" || echo "missing")
            if echo "$SPEC_OK" | grep -q "cellswarm-worker-spec"; then
                DRAFT_STATUS="${DRAFT_STATUS}, spec binary OK"
            else
                DRAFT_STATUS="${DRAFT_STATUS}, NO spec binary"
            fi
        fi

        # Check RAM
        RAM=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL shell cat /proc/meminfo 2>/dev/null" | head -1 | awk '{print $2}')
        RAM_GB=$(echo "scale=1; ${RAM:-0}/1048576" | bc 2>/dev/null || echo "?")

        echo "online, ${RAM_GB}GB RAM, $TARGET_STATUS${DRAFT_STATUS}"
    done
    exit 0
fi

# --- Determine what to deploy ---
DEPLOY_TARGET=false
DEPLOY_DRAFT=false
DEPLOY_SPEC_BIN=false

case "$MODE" in
    --target)     DEPLOY_TARGET=true ;;
    --draft)      DEPLOY_DRAFT=true ;;
    --both)       DEPLOY_TARGET=true; DEPLOY_DRAFT=true ;;
    --spec-binary) DEPLOY_SPEC_BIN=true ;;
    --all)        DEPLOY_TARGET=true; DEPLOY_DRAFT=true; DEPLOY_SPEC_BIN=true ;;
    *)
        echo "ERROR: Unknown mode '$MODE'. Use --target, --draft, --both, --spec-binary, --all, or --status"
        exit 1
        ;;
esac

# --- Ensure Q4_0 target model exists ---
if $DEPLOY_TARGET; then
    if [ ! -f "$MODEL_Q4_0" ]; then
        echo "Q4_0 target model not found at: $MODEL_Q4_0"

        # Try to download from HuggingFace
        echo ""
        echo "Attempting to download from HuggingFace..."
        echo "  URL: TheBloke/deepseek-coder-33B-instruct-GGUF"

        if command -v huggingface-cli &>/dev/null; then
            huggingface-cli download TheBloke/deepseek-coder-33B-instruct-GGUF \
                deepseek-coder-33b-instruct.Q4_0.gguf \
                --local-dir "$(dirname "$MODEL_Q4_0")" && {
                echo "  Download complete."
            } || {
                echo "  Download failed. Trying wget..."
                wget -O "$MODEL_Q4_0" \
                    "https://huggingface.co/TheBloke/deepseek-coder-33B-instruct-GGUF/resolve/main/deepseek-coder-33b-instruct.Q4_0.gguf?download=true" || {
                    echo ""
                    echo "  Download failed. Attempting to quantize from Q4_K_M..."
                    if [ -f "$MODEL_Q4_K_M" ] && [ -f "$LLAMA_QUANTIZE" ]; then
                        echo "  Quantizing Q4_K_M -> Q4_0 (this may take a while)..."
                        "$LLAMA_QUANTIZE" "$MODEL_Q4_K_M" "$MODEL_Q4_0" Q4_0
                        echo "  Quantization complete."
                    else
                        echo "ERROR: Cannot obtain Q4_0 model."
                        echo "  No Q4_K_M model at: $MODEL_Q4_K_M"
                        echo "  No llama-quantize at: $LLAMA_QUANTIZE"
                        echo ""
                        echo "Options:"
                        echo "  1. Download: huggingface-cli download TheBloke/deepseek-coder-33B-instruct-GGUF deepseek-coder-33b-instruct.Q4_0.gguf --local-dir ~/models/"
                        echo "  2. Quantize: llama-quantize ~/models/deepseek-coder-33b-instruct.Q4_K_M.gguf ~/models/deepseek-coder-33b-instruct.Q4_0.gguf Q4_0"
                        exit 1
                    fi
                }
            }
        else
            wget -O "$MODEL_Q4_0" \
                "https://huggingface.co/TheBloke/deepseek-coder-33B-instruct-GGUF/resolve/main/deepseek-coder-33b-instruct.Q4_0.gguf?download=true" || {
                echo ""
                echo "  Download failed. Attempting to quantize from Q4_K_M..."
                if [ -f "$MODEL_Q4_K_M" ] && [ -f "$LLAMA_QUANTIZE" ]; then
                    echo "  Quantizing Q4_K_M -> Q4_0 (this may take a while)..."
                    "$LLAMA_QUANTIZE" "$MODEL_Q4_K_M" "$MODEL_Q4_0" Q4_0
                    echo "  Quantization complete."
                else
                    echo "ERROR: Cannot obtain Q4_0 model."
                    echo "  Options:"
                    echo "  1. Download: huggingface-cli download TheBloke/deepseek-coder-33B-instruct-GGUF deepseek-coder-33b-instruct.Q4_0.gguf --local-dir ~/models/"
                    echo "  2. Quantize: llama-quantize ~/models/deepseek-coder-33b-instruct.Q4_K_M.gguf ~/models/deepseek-coder-33b-instruct.Q4_0.gguf Q4_0"
                    exit 1
                fi
            }
        fi
    fi

    if [ ! -f "$MODEL_Q4_0" ]; then
        echo "ERROR: Q4_0 model still not found after download/quantize attempt."
        exit 1
    fi

    MODEL_SIZE=$(stat -c%s "$MODEL_Q4_0")
    MODEL_SIZE_HR=$(numfmt --to=iec "$MODEL_SIZE")
    echo "Target model: $MODEL_Q4_0 ($MODEL_SIZE_HR)"
fi

# --- Ensure Q4_0 draft model exists ---
if $DEPLOY_DRAFT; then
    if [ ! -f "$DRAFT_Q4_0" ]; then
        echo "Q4_0 draft model not found at: $DRAFT_Q4_0"
        echo "Attempting download..."

        if command -v huggingface-cli &>/dev/null; then
            huggingface-cli download TheBloke/deepseek-coder-1.3b-instruct-GGUF \
                deepseek-coder-1.3b-instruct.Q4_0.gguf \
                --local-dir "$(dirname "$DRAFT_Q4_0")" || {
                echo "  huggingface-cli failed, trying wget..."
                wget -O "$DRAFT_Q4_0" \
                    "https://huggingface.co/TheBloke/deepseek-coder-1.3b-instruct-GGUF/resolve/main/deepseek-coder-1.3b-instruct.Q4_0.gguf?download=true" || {
                    # Quantize from Q4_K_M as last resort
                    if [ -f "$DRAFT_Q4_K_M" ] && [ -f "$LLAMA_QUANTIZE" ]; then
                        echo "  Quantizing draft Q4_K_M -> Q4_0..."
                        "$LLAMA_QUANTIZE" "$DRAFT_Q4_K_M" "$DRAFT_Q4_0" Q4_0
                    else
                        echo "ERROR: Cannot obtain Q4_0 draft model."
                        exit 1
                    fi
                }
            }
        else
            wget -O "$DRAFT_Q4_0" \
                "https://huggingface.co/TheBloke/deepseek-coder-1.3b-instruct-GGUF/resolve/main/deepseek-coder-1.3b-instruct.Q4_0.gguf?download=true" || {
                if [ -f "$DRAFT_Q4_K_M" ] && [ -f "$LLAMA_QUANTIZE" ]; then
                    echo "  Quantizing draft Q4_K_M -> Q4_0..."
                    "$LLAMA_QUANTIZE" "$DRAFT_Q4_K_M" "$DRAFT_Q4_0" Q4_0
                else
                    echo "ERROR: Cannot obtain Q4_0 draft model."
                    exit 1
                fi
            }
        fi
    fi

    if [ ! -f "$DRAFT_Q4_0" ]; then
        echo "ERROR: Q4_0 draft model still not found."
        exit 1
    fi

    DRAFT_SIZE=$(stat -c%s "$DRAFT_Q4_0")
    DRAFT_SIZE_HR=$(numfmt --to=iec "$DRAFT_SIZE")
    echo "Draft model:  $DRAFT_Q4_0 ($DRAFT_SIZE_HR)"
fi

# --- Ensure speculative binary exists ---
if $DEPLOY_SPEC_BIN; then
    if [ ! -f "$SPEC_BINARY" ]; then
        echo "ERROR: $SPEC_BINARY not found. Run ./scripts/build_cellswarm.sh first."
        exit 1
    fi
    SPEC_SIZE_HR=$(stat -c%s "$SPEC_BINARY" | numfmt --to=iec)
    echo "Spec binary:  $SPEC_BINARY ($SPEC_SIZE_HR)"
fi

# --- Stage files to WinPC ---
echo ""
echo "Staging files to WinPC..."
ssh -o ConnectTimeout=10 "$SSH_HOST" "mkdir -p \"$WIN_STAGING\"" 2>/dev/null || true

if $DEPLOY_TARGET; then
    # Check if already staged (avoid re-uploading ~17GB)
    WIN_SIZE=$(ssh -o ConnectTimeout=10 "$SSH_HOST" "wc -c < \"${WIN_STAGING}\\deepseek-coder-33b-instruct.Q4_0.gguf\" 2>/dev/null" | tr -d '\r ' || echo "0")
    LOCAL_SIZE=$(stat -c%s "$MODEL_Q4_0")
    if [ "${WIN_SIZE:-0}" = "$LOCAL_SIZE" ]; then
        echo "  Target model already staged on WinPC (size matches), skipping upload."
    else
        echo "  Uploading target model to WinPC (~17GB, this will take a while)..."
        scp -o ConnectTimeout=30 "$MODEL_Q4_0" "${SSH_HOST}:${WIN_STAGING}\\deepseek-coder-33b-instruct.Q4_0.gguf"
        echo "  Target model staged."
    fi
fi

if $DEPLOY_DRAFT; then
    echo "  Uploading draft model to WinPC (~850MB)..."
    scp -o ConnectTimeout=30 "$DRAFT_Q4_0" "${SSH_HOST}:${WIN_STAGING}\\deepseek-coder-1.3b-instruct.Q4_0.gguf"
    echo "  Draft model staged."
fi

if $DEPLOY_SPEC_BIN; then
    echo "  Uploading cellswarm-worker-spec to WinPC..."
    scp -o ConnectTimeout=30 "$SPEC_BINARY" "${SSH_HOST}:${WIN_STAGING}\\cellswarm-worker-spec"
    echo "  Spec binary staged."
fi

# --- Deploy to phones ---
echo ""
SUCCESS=0
FAIL=0

for idx in "${!PHONES[@]}"; do
    SERIAL="${PHONES[$idx]}"
    RANK=$((idx + 1))
    echo "--- Deploying to $SERIAL (rank $RANK) ---"

    # Check device online
    ONLINE=$(ssh -o ConnectTimeout=5 "$SSH_HOST" "$ADB -s $SERIAL get-state" 2>/dev/null | tr -d '\r' || echo "offline")
    if [ "$ONLINE" != "device" ]; then
        echo "  SKIPPED — device offline ($ONLINE)"
        FAIL=$((FAIL + 1))
        continue
    fi

    # Create directories
    ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell mkdir -p $REMOTE_BIN $REMOTE_MODELS" 2>/dev/null

    # Deploy target model to ALL phones
    if $DEPLOY_TARGET; then
        # Check if already on phone
        PHONE_SIZE=$(ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell wc -c < $REMOTE_MODEL_Q4_0 2>/dev/null" | tr -d '\r' || echo "0")
        LOCAL_SIZE=$(stat -c%s "$MODEL_Q4_0")
        if [ "${PHONE_SIZE:-0}" = "$LOCAL_SIZE" ]; then
            echo "  Target model already on phone (size matches), skipping."
        else
            echo "  Pushing target model (~17GB, USB 3.0 ~60s)..."
            ssh -o ConnectTimeout=600 "$SSH_HOST" \
                "$ADB -s $SERIAL push \"${WIN_STAGING}\\deepseek-coder-33b-instruct.Q4_0.gguf\" $REMOTE_MODEL_Q4_0" 2>&1 | tail -1
        fi
    fi

    # Deploy draft model to rank 0 (first phone) ONLY
    if $DEPLOY_DRAFT && [ "$idx" -eq 0 ]; then
        echo "  Pushing draft model to rank 0 (~850MB)..."
        ssh -o ConnectTimeout=120 "$SSH_HOST" \
            "$ADB -s $SERIAL push \"${WIN_STAGING}\\deepseek-coder-1.3b-instruct.Q4_0.gguf\" $REMOTE_DRAFT_Q4_0" 2>&1 | tail -1
    fi

    # Deploy speculative binary to rank 0 ONLY
    if $DEPLOY_SPEC_BIN && [ "$idx" -eq 0 ]; then
        echo "  Pushing cellswarm-worker-spec to rank 0..."
        ssh -o ConnectTimeout=60 "$SSH_HOST" \
            "$ADB -s $SERIAL push \"${WIN_STAGING}\\cellswarm-worker-spec\" $REMOTE_SPEC_BIN" 2>&1 | tail -1
        ssh -o ConnectTimeout=10 "$SSH_HOST" "$ADB -s $SERIAL shell chmod 755 $REMOTE_SPEC_BIN" 2>/dev/null
    fi

    echo "  Done."
    SUCCESS=$((SUCCESS + 1))
done

echo ""
echo "============================================"
echo " Q4_0 DEPLOYMENT COMPLETE"
echo "============================================"
echo "  Success: $SUCCESS / $N_PHONES"
if [ "$FAIL" -gt 0 ]; then
    echo "  Failed:  $FAIL"
fi
echo ""
echo "Verify with: ./scripts/deploy_model.sh $N_PHONES --status"
echo "============================================"
