#!/usr/bin/env bash
# Cross-compile llama.cpp rpc-server for aarch64 Android.
#
# Prerequisites:
#   - Android NDK installed (set ANDROID_NDK_HOME)
#   - CMake >= 3.14
#
# Usage: ./build_rpc_server.sh [/path/to/llama.cpp]

set -euo pipefail

LLAMA_CPP_DIR="${1:-$HOME/llama.cpp}"
BUILD_DIR="$LLAMA_CPP_DIR/build-android"
NDK="${ANDROID_NDK_HOME:-$HOME/Android/Sdk/ndk/26.1.10909125}"
API_LEVEL=28  # Android 9+
OUTPUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/bin"

if [ ! -d "$LLAMA_CPP_DIR" ]; then
    echo "llama.cpp not found at $LLAMA_CPP_DIR"
    echo "Usage: $0 /path/to/llama.cpp"
    exit 1
fi

if [ ! -d "$NDK" ]; then
    echo "Android NDK not found at $NDK"
    echo "Set ANDROID_NDK_HOME or install via Android Studio SDK Manager"
    exit 1
fi

TOOLCHAIN="$NDK/build/cmake/android.toolchain.cmake"

echo "Building rpc-server for aarch64 Android..."
echo "  llama.cpp: $LLAMA_CPP_DIR"
echo "  NDK:       $NDK"
echo "  API level: $API_LEVEL"

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

cmake .. \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
    -DANDROID_ABI=arm64-v8a \
    -DANDROID_PLATFORM=android-$API_LEVEL \
    -DGGML_RPC=ON \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF \
    -DLLAMA_BUILD_SERVER=OFF \
    -DBUILD_SHARED_LIBS=OFF

cmake --build . --target rpc-server -j$(nproc)

mkdir -p "$OUTPUT_DIR"
cp bin/rpc-server "$OUTPUT_DIR/rpc-server"

echo ""
echo "Built: $OUTPUT_DIR/rpc-server"
echo ""
echo "Push to devices with:"
echo "  adb-llm devices list"
echo "  for serial in \$(adb devices | tail -n +2 | awk '{print \$1}'); do"
echo "    adb -s \$serial push $OUTPUT_DIR/rpc-server /data/local/tmp/adb-llm/bin/rpc-server"
echo "    adb -s \$serial shell chmod +x /data/local/tmp/adb-llm/bin/rpc-server"
echo "  done"
