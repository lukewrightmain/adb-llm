#!/bin/bash
# Build tcp_hop_bench for both ARM64 (Android) and x86_64 (host)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SRC="$PROJECT_DIR/benchmarks/tcp_hop_bench.c"
BIN_DIR="$PROJECT_DIR/bin"

NDK="${ANDROID_NDK:-$HOME/android-ndk/android-ndk-r26d}"
CLANG="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android34-clang"

mkdir -p "$BIN_DIR"

echo "=== Building ARM64 (Android) ==="
if [ -f "$CLANG" ]; then
    "$CLANG" -O2 -static -o "$BIN_DIR/tcp_hop_bench" "$SRC"
    ls -lh "$BIN_DIR/tcp_hop_bench"
    echo "ARM64 build OK"
else
    echo "WARNING: NDK not found at $NDK, skipping ARM64 build"
fi

echo ""
echo "=== Building x86_64 (host) ==="
gcc -O2 -o "$BIN_DIR/tcp_hop_bench_x86" "$SRC"
ls -lh "$BIN_DIR/tcp_hop_bench_x86"
echo "x86_64 build OK"

echo ""
echo "Done. Deploy to phones with:"
echo "  adb push $BIN_DIR/tcp_hop_bench /data/local/tmp/adb-llm/bin/"
