#!/usr/bin/env bash
# Deploy Qwen3.5-35B-A3B (MoE) model to ethernet phones.
# Downloads from HuggingFace if not cached, then pushes to phones via ADB.
#
# Usage:
#   ./scripts/deploy_qwen_moe.sh 12              # deploy to first 12 phones
#   ./scripts/deploy_qwen_moe.sh 12 --draft      # also deploy draft model
#   ./scripts/deploy_qwen_moe.sh --status         # check which phones have model

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

N_PHONES=${1:-12}
DEPLOY_DRAFT=false
STATUS_ONLY=false

shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        --draft)   DEPLOY_DRAFT=true ;;
        --status)  STATUS_ONLY=true ;;
        *)         echo "Unknown: $1"; exit 1 ;;
    esac
    shift
done

ADB="$HOME/.local/bin/adb"

# Model details
MODEL_REPO="unsloth/Qwen3.5-35B-A3B-GGUF"
MODEL_FILE="Qwen3.5-35B-A3B-Q4_K_M.gguf"
MODEL_LOCAL="$PROJECT_DIR/models/$MODEL_FILE"
MODEL_REMOTE="/data/local/tmp/cellswarm/models/$MODEL_FILE"

# Draft model — try Qwen2.5-1.5B (may share tokenizer family)
DRAFT_REPO="Qwen/Qwen2.5-1.5B-Instruct-GGUF"
DRAFT_FILE="qwen2.5-1.5b-instruct-q4_k_m.gguf"
DRAFT_LOCAL="$HOME/.cache/huggingface/hub/models--Qwen--Qwen2.5-1.5B-Instruct-GGUF/$DRAFT_FILE"
DRAFT_REMOTE="/data/local/tmp/cellswarm/models/qwen2.5-1.5b-instruct.Q4_K_M.gguf"

ALL_PHONES=(
    10.105.0.41 10.105.0.42 10.105.0.44 10.105.0.45 10.105.0.48
    10.105.0.12 10.105.0.13 10.105.0.17 10.105.0.19 10.105.0.20
    10.105.0.24 10.105.0.28 10.105.0.29 10.105.0.30 10.105.0.31
    10.105.0.32 10.105.0.156 10.105.0.36 10.105.0.38 10.105.0.40
)

PHONES=("${ALL_PHONES[@]:0:$N_PHONES}")

adb_shell() {
    local ip=$1; shift
    $ADB -s "${ip}:5555" shell "$@"
}

if $STATUS_ONLY; then
    echo "Model status on phones:"
    for ip in "${ALL_PHONES[@]}"; do
        size=$(adb_shell "$ip" "stat -c %s $MODEL_REMOTE 2>/dev/null" 2>/dev/null | tr -d '\r' || echo "0")
        if [ "$size" -gt 1000000 ]; then
            size_mb=$((size / 1048576))
            echo "  $ip: ${size_mb}MB"
        else
            echo "  $ip: NOT DEPLOYED"
        fi
    done
    exit 0
fi

echo "============================================"
echo " Qwen3.5-35B-A3B (MoE) Deployment"
echo "============================================"
echo "  Architecture: MoE — 35B total, 3B active"
echo "  Experts:      256 (8 routed + 1 shared)"
echo "  Layers:       40"
echo "  Quant:        Q4_K_M (~21.2GB)"
echo "  Phones:       $N_PHONES"
echo ""

# Download model if not cached
if [ ! -f "$MODEL_LOCAL" ]; then
    echo "Downloading $MODEL_FILE from HuggingFace..."
    # Try huggingface-cli first, fall back to direct download
    if command -v huggingface-cli &>/dev/null; then
        huggingface-cli download "$MODEL_REPO" "$MODEL_FILE" --local-dir "$(dirname "$MODEL_LOCAL")"
    else
        MODEL_LOCAL="$PROJECT_DIR/models/$MODEL_FILE"
        mkdir -p "$(dirname "$MODEL_LOCAL")"
        echo "huggingface-cli not found. Download manually:"
        echo "  huggingface-cli download $MODEL_REPO $MODEL_FILE --local-dir $(dirname "$MODEL_LOCAL")"
        echo "  or: wget https://huggingface.co/$MODEL_REPO/resolve/main/$MODEL_FILE -O $MODEL_LOCAL"
        exit 1
    fi
fi

echo "Model cached at: $MODEL_LOCAL"
echo "Size: $(du -h "$MODEL_LOCAL" | cut -f1)"
echo ""

# Deploy to phones
echo "Deploying to $N_PHONES phones..."
for ip in "${PHONES[@]}"; do
    echo -n "  $ip: "
    # Check if already deployed
    remote_size=$(adb_shell "$ip" "stat -c %s $MODEL_REMOTE 2>/dev/null" 2>/dev/null | tr -d '\r' || echo "0")
    local_size=$(stat -c %s "$MODEL_LOCAL" 2>/dev/null || echo "0")
    if [ "$remote_size" = "$local_size" ] && [ "$remote_size" -gt 1000000 ]; then
        echo "already deployed ($(( remote_size / 1048576 ))MB)"
        continue
    fi

    adb_shell "$ip" "mkdir -p /data/local/tmp/cellswarm/models" 2>/dev/null || true
    $ADB -s "${ip}:5555" push "$MODEL_LOCAL" "$MODEL_REMOTE" 2>&1 | tail -1
done

# Deploy draft model if requested
if $DEPLOY_DRAFT; then
    echo ""
    echo "--- Draft model: $DRAFT_FILE ---"
    if [ ! -f "$DRAFT_LOCAL" ]; then
        echo "Draft model not cached. Download:"
        echo "  huggingface-cli download $DRAFT_REPO $DRAFT_FILE"
        echo "Skipping draft deployment."
    else
        echo "Deploying draft to $N_PHONES phones..."
        for ip in "${PHONES[@]}"; do
            echo -n "  $ip: "
            $ADB -s "${ip}:5555" push "$DRAFT_LOCAL" "$DRAFT_REMOTE" 2>&1 | tail -1
        done
    fi
fi

echo ""
echo "============================================"
echo " Deployment complete"
echo "============================================"
echo ""
echo "To launch:"
echo "  MODEL_FAMILY=qwen3.5-moe ./scripts/launch_cellswarm_master.sh $N_PHONES"
echo ""
echo "Note: MoE loads ALL expert weights per layer."
echo "  40 layers / $N_PHONES phones = $((40 / N_PHONES)) layers/phone"
echo "  ~21.2GB / $N_PHONES = ~$((21200 / N_PHONES))MB per phone — should fit in 5GB RAM"
