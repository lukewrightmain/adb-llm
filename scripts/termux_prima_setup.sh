#!/data/data/com.termux/files/usr/bin/bash
# Prima.cpp setup script for Termux on Android
# Run this inside Termux: bash /sdcard/termux_prima_setup.sh
#
# This installs dependencies, clones prima.cpp, and builds it.
# The 33B model is already on the phone at /data/local/tmp/adb-llm/models/

set -e

echo "============================================"
echo " Prima.cpp Termux Setup"
echo "============================================"
echo ""

# Step 1: Upgrade ALL packages first (avoids SSL/ngtcp2 mismatch), then install deps
# NOTE: Use 'apt' directly, NOT 'pkg', because pkg uses curl for mirror checks
#       and curl may be broken by the ngtcp2/openssl version mismatch.
echo "[1/4] Upgrading packages and installing dependencies..."
apt update -y
apt full-upgrade -y -o Dpkg::Options::="--force-confnew"
apt install -y clang make cmake git wget libzmq curl
echo "  Dependencies installed."
echo ""

# Step 2: Clone prima.cpp
echo "[2/4] Cloning prima.cpp..."
cd ~
if [ -d "prima.cpp" ]; then
    echo "  prima.cpp already exists, pulling latest..."
    cd prima.cpp && git pull && cd ~
else
    git clone https://gitee.com/zonghang-li/prima.cpp.git
fi
echo "  Clone complete."
echo ""

# Step 3: Build prima.cpp
echo "[3/4] Building prima.cpp..."
cd ~/prima.cpp

# Detect if this should be a rank 0 (head) build
# Pass RANK0=1 as env var to build with HiGHS support
if [ "${RANK0:-0}" = "1" ]; then
    echo "  Building as RANK 0 (head device) with HiGHS..."

    # Build HiGHS first
    if [ ! -f /data/data/com.termux/files/usr/lib/libhighs.so ]; then
        echo "  Building HiGHS solver..."
        cd ~
        if [ ! -d "HiGHS" ]; then
            git clone https://github.com/ERGO-Code/HiGHS.git
        fi
        cd HiGHS
        mkdir -p build && cd build
        cmake ..
        make -j$(nproc)
        make install
        cd ~/prima.cpp
    fi

    make USE_HIGHS=1 -j$(nproc)
else
    echo "  Building as worker device..."
    make -j$(nproc)
fi
echo "  Build complete."
echo ""

# Step 4: Verify
echo "[4/4] Verifying..."
if [ -f ~/prima.cpp/llama-cli ]; then
    echo "  ✓ llama-cli built successfully"
else
    echo "  ✗ llama-cli not found — build may have failed"
    exit 1
fi

# Check model access
MODEL_33B="/data/local/tmp/adb-llm/models/deepseek-coder-33b-instruct.Q4_K_M.gguf"
MODEL_1B="/data/local/tmp/adb-llm/models/deepseek-coder-1.3b-instruct.Q4_K_M.gguf"
if [ -r "$MODEL_33B" ]; then
    echo "  ✓ 33B model accessible at $MODEL_33B"
else
    echo "  ✗ Cannot read 33B model — may need different path"
fi
if [ -r "$MODEL_1B" ]; then
    echo "  ✓ 1.3B model accessible at $MODEL_1B"
else
    echo "  ✗ Cannot read 1.3B model"
fi

echo ""
echo "============================================"
echo " Setup complete!"
echo ""
echo " Get this phone's IP:"
echo "   ifconfig | grep 'inet '"
echo ""
echo " Quick single-phone test (1.3B model):"
echo "   cd ~/prima.cpp"
echo "   ./llama-cli -m $MODEL_1B -c 512 -n 64 -p 'Hello world' --no-mmap -t 4"
echo ""
echo " Multi-phone ring (see instructions from server):"
echo "   cd ~/prima.cpp"
echo "   ./llama-cli -m $MODEL_33B --world N --rank R --master <rank0_ip> --next <next_ip> --prefetch --no-mmap -t 4"
echo "============================================"
