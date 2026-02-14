#!/usr/bin/env bash
# One-time setup for each Android device.
# Usage: ./setup_device.sh [SERIAL]
#
# Creates remote directories and optionally pushes rpc-server binary.

set -euo pipefail

SERIAL="${1:-}"
REMOTE_BASE="/data/local/tmp/adb-llm"
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

# Check if rpc-server exists already
echo "  Checking for rpc-server..."
if adb -s "$SERIAL" shell "ls $REMOTE_BIN/rpc-server" 2>/dev/null; then
    echo "  rpc-server already present"
else
    # Check if there's one from exo
    if adb -s "$SERIAL" shell "ls /data/local/tmp/rpc-server" 2>/dev/null; then
        echo "  Copying rpc-server from exo location..."
        adb -s "$SERIAL" shell "cp /data/local/tmp/rpc-server $REMOTE_BIN/rpc-server"
        adb -s "$SERIAL" shell "chmod +x $REMOTE_BIN/rpc-server"
    else
        echo "  [WARNING] No rpc-server found. Build and push with:"
        echo "    adb -s $SERIAL push rpc-server $REMOTE_BIN/rpc-server"
        echo "    adb -s $SERIAL shell chmod +x $REMOTE_BIN/rpc-server"
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
