#!/usr/bin/env bash
# One-time setup for each Android device.
# Usage: ./setup_device.sh [SERIAL]
#
# Creates remote directories and optionally pushes swarm-rpc binary.

set -euo pipefail

SERIAL="${1:-}"
REMOTE_BASE="/data/local/tmp/cellswarm"
REMOTE_MODELS="$REMOTE_BASE/models"
REMOTE_BIN="$REMOTE_BASE/bin"

if [ -z "$SERIAL" ]; then
    echo "Usage: $0 <device-serial>"
    echo ""
    echo "Available devices:"
    adb devices -l | tail -n +2
    exit 1
fi

echo "Setting up device: $SERIAL"

# Create directories
echo "  Creating directories..."
adb -s "$SERIAL" shell "mkdir -p $REMOTE_MODELS $REMOTE_BIN"

# Check if swarm-rpc exists already
echo "  Checking for swarm-rpc..."
if adb -s "$SERIAL" shell "ls $REMOTE_BIN/swarm-rpc" 2>/dev/null; then
    echo "  swarm-rpc already present"
else
    # Check if there's one from exo
    if adb -s "$SERIAL" shell "ls /data/local/tmp/swarm-rpc" 2>/dev/null; then
        echo "  Copying swarm-rpc from exo location..."
        adb -s "$SERIAL" shell "cp /data/local/tmp/swarm-rpc $REMOTE_BIN/swarm-rpc"
        adb -s "$SERIAL" shell "chmod +x $REMOTE_BIN/swarm-rpc"
    else
        echo "  [WARNING] No swarm-rpc found. Build and push with:"
        echo "    adb -s $SERIAL push swarm-rpc $REMOTE_BIN/swarm-rpc"
        echo "    adb -s $SERIAL shell chmod +x $REMOTE_BIN/swarm-rpc"
    fi
fi

# Device info
echo ""
echo "  Device info:"
echo "    Model:   $(adb -s "$SERIAL" shell getprop ro.product.model)"
echo "    Android: $(adb -s "$SERIAL" shell getprop ro.build.version.release)"
echo "    CPU:     $(adb -s "$SERIAL" shell getprop ro.product.cpu.abi)"
echo "    RAM:     $(adb -s "$SERIAL" shell cat /proc/meminfo | head -1)"
echo "    Storage: $(adb -s "$SERIAL" shell df /data | tail -1)"

echo ""
echo "Setup complete for $SERIAL"
