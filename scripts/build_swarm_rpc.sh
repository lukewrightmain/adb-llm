#!/usr/bin/env bash
# Cross-compile llama.cpp swarm-rpc for aarch64 Android.
#
# Prerequisites:
#   - CMake >= 3.14  (auto-detected from ~/.local/bin or PATH)
#   - Android NDK    (auto-downloaded if not present)
#
# Usage: ./build_swarm_rpc.sh [/path/to/llama.cpp]

set -euo pipefail

LLAMA_CPP_DIR="${1:-$HOME/llama.cpp}"
BUILD_DIR="$LLAMA_CPP_DIR/build-android"
OUTPUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/bin"
API_LEVEL=28  # Android 9+

# NDK location — auto-download if missing
NDK_DIR="$HOME/android-ndk"
NDK_VERSION="r26d"
NDK_ZIP_URL="https://dl.google.com/android/repository/android-ndk-${NDK_VERSION}-linux.zip"

NDK="${ANDROID_NDK_HOME:-$NDK_DIR/android-ndk-${NDK_VERSION}}"

# Add ~/.local/bin to PATH for cmake/ninja installed there
export PATH="$HOME/.local/bin:$PATH"

# Validate llama.cpp source
if [ ! -d "$LLAMA_CPP_DIR" ]; then
    echo "ERROR: llama.cpp not found at $LLAMA_CPP_DIR"
    echo "Usage: $0 /path/to/llama.cpp"
    exit 1
fi

# Validate cmake
if ! command -v cmake &>/dev/null; then
    echo "ERROR: cmake not found. Install it first."
    exit 1
fi

# Auto-download NDK if not present
if [ ! -d "$NDK" ]; then
    echo "Android NDK not found at $NDK"
    echo "Downloading NDK $NDK_VERSION..."
    mkdir -p "$NDK_DIR"
    NDK_ZIP="/tmp/android-ndk-${NDK_VERSION}.zip"
    if [ ! -f "$NDK_ZIP" ]; then
        wget -q --show-progress "$NDK_ZIP_URL" -O "$NDK_ZIP"
    fi
    echo "Extracting NDK (this takes a minute)..."
    unzip -q "$NDK_ZIP" -d "$NDK_DIR"
    echo "NDK installed to $NDK"
fi

TOOLCHAIN="$NDK/build/cmake/android.toolchain.cmake"

if [ ! -f "$TOOLCHAIN" ]; then
    echo "ERROR: NDK toolchain not found at $TOOLCHAIN"
    exit 1
fi

echo "========================================="
echo "Building swarm-rpc for aarch64 Android"
echo "========================================="
echo "  llama.cpp: $LLAMA_CPP_DIR"
echo "  NDK:       $NDK"
echo "  API level: $API_LEVEL"
echo "  Output:    $OUTPUT_DIR/swarm-rpc"
echo ""

# Cross-compile LZ4 static library for Android
LZ4_DIR="$BUILD_DIR/_lz4"
LZ4_BUILD="$BUILD_DIR/_lz4_build"
if [ ! -d "$LZ4_DIR" ]; then
    echo "Downloading LZ4 source..."
    git clone --depth 1 --branch v1.10.0 https://github.com/lz4/lz4.git "$LZ4_DIR"
fi
mkdir -p "$LZ4_BUILD"
CC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android${API_LEVEL}-clang"
AR="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-ar"
echo "Building LZ4 static library for Android..."
"$CC" -c "$LZ4_DIR/lib/lz4.c" -O3 -o "$LZ4_BUILD/lz4.o"
"$AR" rcs "$LZ4_BUILD/liblz4.a" "$LZ4_BUILD/lz4.o"
echo "LZ4 built: $LZ4_BUILD/liblz4.a"

# Clean previous llama.cpp build if it exists
rm -rf "$BUILD_DIR/CMakeCache.txt" "$BUILD_DIR/CMakeFiles"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

cmake .. \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
    -DANDROID_ABI=arm64-v8a \
    -DANDROID_PLATFORM=android-$API_LEVEL \
    -DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16 \
    -DGGML_RPC=ON \
    -DLZ4_LIBRARIES="$LZ4_BUILD/liblz4.a" \
    -DLZ4_INCLUDE_DIRS="$LZ4_DIR/lib" \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF \
    -DLLAMA_BUILD_SERVER=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_OPENMP=OFF

cmake --build . --target swarm-rpc -j$(nproc)

# Copy to output
mkdir -p "$OUTPUT_DIR"

# Find the built binary (may be in bin/ or directly in build dir)
RPC_BIN=""
for candidate in bin/swarm-rpc swarm-rpc examples/rpc/swarm-rpc; do
    if [ -f "$candidate" ]; then
        RPC_BIN="$candidate"
        break
    fi
done

if [ -z "$RPC_BIN" ]; then
    echo "ERROR: swarm-rpc binary not found in build directory"
    echo "Contents of build dir:"
    find . -name "swarm-rpc" -o -name "swarm_rpc" 2>/dev/null
    exit 1
fi

cp "$RPC_BIN" "$OUTPUT_DIR/swarm-rpc"

echo ""
echo "========================================="
echo "SUCCESS: $OUTPUT_DIR/swarm-rpc"
file "$OUTPUT_DIR/swarm-rpc"
ls -lh "$OUTPUT_DIR/swarm-rpc"
echo "========================================="
