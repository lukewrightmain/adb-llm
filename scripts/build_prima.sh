#!/usr/bin/env bash
# Cross-compile prima.cpp for aarch64 Android + native Linux host.
#
# Produces:
#   bin/prima-worker      (ARM64 Android - runs on phones)
#   bin/prima-worker-spec (ARM64 Android - speculative decoding, phone rank 0)
#   bin/prima-host        (x86_64 Linux  - runs on this server as rank 0)
#   bin/prima-host-spec   (x86_64 Linux  - speculative decoding host, rank 0)
#
# Prerequisites:
#   - CMake >= 3.14
#   - Android NDK (auto-downloaded if missing)
#   - libzmq3-dev (for host build, or built from vendor/libzmq)
#
# Usage: ./scripts/build_prima.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PRIMA_DIR="$PROJECT_DIR/vendor/prima.cpp"
LIBZMQ_DIR="$PROJECT_DIR/vendor/libzmq"
CPPZMQ_DIR="$PROJECT_DIR/vendor/cppzmq"
HIGHS_DIR="$PROJECT_DIR/vendor/HiGHS"
OUTPUT_DIR="$PROJECT_DIR/bin"
BUILD_BASE="$PROJECT_DIR/build"

API_LEVEL=28  # Android 9+

# NDK location
NDK_DIR="$HOME/android-ndk"
NDK_VERSION="r26d"
NDK_ZIP_URL="https://dl.google.com/android/repository/android-ndk-${NDK_VERSION}-linux.zip"
NDK="${ANDROID_NDK_HOME:-$NDK_DIR/android-ndk-${NDK_VERSION}}"

export PATH="$HOME/.local/bin:$PATH"

# Validate sources
for dir in "$PRIMA_DIR" "$LIBZMQ_DIR" "$CPPZMQ_DIR"; do
    if [ ! -d "$dir" ]; then
        echo "ERROR: $dir not found. Run vendor setup first."
        exit 1
    fi
done

if ! command -v cmake &>/dev/null; then
    echo "ERROR: cmake not found."
    exit 1
fi

# Auto-download NDK if missing
if [ ! -d "$NDK" ]; then
    echo "Android NDK not found at $NDK — downloading..."
    mkdir -p "$NDK_DIR"
    NDK_ZIP="/tmp/android-ndk-${NDK_VERSION}.zip"
    if [ ! -f "$NDK_ZIP" ]; then
        wget -q --show-progress "$NDK_ZIP_URL" -O "$NDK_ZIP"
    fi
    echo "Extracting NDK..."
    unzip -q "$NDK_ZIP" -d "$NDK_DIR"
fi

TOOLCHAIN="$NDK/build/cmake/android.toolchain.cmake"
if [ ! -f "$TOOLCHAIN" ]; then
    echo "ERROR: NDK toolchain not found at $TOOLCHAIN"
    exit 1
fi

CC_ANDROID="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android${API_LEVEL}-clang"
CXX_ANDROID="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android${API_LEVEL}-clang++"
AR_ANDROID="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-ar"
RANLIB_ANDROID="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-ranlib"

mkdir -p "$OUTPUT_DIR" "$BUILD_BASE"

echo "============================================"
echo " prima.cpp build — ARM64 Android + x86_64 host"
echo "============================================"
echo "  prima.cpp: $PRIMA_DIR"
echo "  NDK:       $NDK"
echo "  Output:    $OUTPUT_DIR/"
echo ""

# ==========================================
# Stage A: libzmq static for ARM64 Android
# ==========================================
echo ">>> Stage A: Building libzmq (ARM64 Android static)..."

ZMQ_BUILD_ANDROID="$BUILD_BASE/libzmq-android"
ZMQ_INSTALL_ANDROID="$BUILD_BASE/libzmq-android-install"

if [ -f "$ZMQ_INSTALL_ANDROID/lib/libzmq.a" ]; then
    echo "  libzmq already built, skipping."
else
    rm -rf "$ZMQ_BUILD_ANDROID"
    mkdir -p "$ZMQ_BUILD_ANDROID"
    cd "$ZMQ_BUILD_ANDROID"

    cmake "$LIBZMQ_DIR" \
        -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
        -DANDROID_ABI=arm64-v8a \
        -DANDROID_PLATFORM=android-$API_LEVEL \
        -DCMAKE_INSTALL_PREFIX="$ZMQ_INSTALL_ANDROID" \
        -DBUILD_SHARED_LIBS=OFF \
        -DBUILD_STATIC=ON \
        -DWITH_LIBSODIUM=OFF \
        -DWITH_TLS=OFF \
        -DZMQ_BUILD_TESTS=OFF \
        -DWITH_DOCS=OFF \
        -DWITH_PERF_TOOL=OFF \
        -DCMAKE_BUILD_TYPE=Release

    cmake --build . -j"$(nproc)"
    cmake --install .
    # Remove shared lib to force static linking when cross-compiling
    rm -f "$ZMQ_INSTALL_ANDROID/lib/libzmq.so"*
    echo "  libzmq installed to $ZMQ_INSTALL_ANDROID"
fi

# ==========================================
# Stage B: prima-worker for ARM64 Android
# ==========================================
# Vulkan GPU acceleration: set ENABLE_VULKAN=1 to enable.
# NOTE: Adreno 660 (Snapdragon 888) crashes with vk::DeviceLostError on compute
# shaders — even with 1 GPU layer. Needs Adreno 750+ (Snapdragon 8 Gen 3).
ENABLE_VULKAN="${ENABLE_VULKAN:-0}"

if [ "$ENABLE_VULKAN" = "1" ]; then
    echo ""
    echo ">>> Stage B: Building prima-worker (ARM64 Android + Vulkan)..."
else
    echo ""
    echo ">>> Stage B: Building prima-worker (ARM64 Android, CPU only)..."
fi

PRIMA_BUILD_ANDROID="$BUILD_BASE/prima-android"
rm -rf "$PRIMA_BUILD_ANDROID/CMakeCache.txt" "$PRIMA_BUILD_ANDROID/CMakeFiles"
mkdir -p "$PRIMA_BUILD_ANDROID"
cd "$PRIMA_BUILD_ANDROID"

export CC="$CC_ANDROID"
export CXX="$CXX_ANDROID"
export AR="$AR_ANDROID"
export RANLIB="$RANLIB_ANDROID"

cd "$PRIMA_DIR"
make clean 2>/dev/null || true

# Base flags (CPU-only)
WORKER_CPPFLAGS="-DPRIMA_DEBUG -I${ZMQ_INSTALL_ANDROID}/include -I${CPPZMQ_DIR} -DGGML_USE_CPU -D__ANDROID__"
WORKER_LDFLAGS="-L${ZMQ_INSTALL_ANDROID}/lib -lzmq -static-libstdc++"
WORKER_MAKE_EXTRAS=""

if [ "$ENABLE_VULKAN" = "1" ]; then
    # Vulkan paths
    VK_INCLUDE="$HOME/Vulkan-Headers/include"
    VK_LIB="$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/aarch64-linux-android/$API_LEVEL"
    GLSLC="$NDK/shader-tools/linux-x86_64/glslc"
    VK_SHADERS_GEN="$BUILD_BASE/vulkan-shaders-gen"
    VK_SHADERS_HPP="$PRIMA_DIR/ggml/src/ggml-vulkan-shaders.hpp"
    VK_SHADERS_CPP="$PRIMA_DIR/ggml/src/ggml-vulkan-shaders.cpp"

    # Build shader generator (host x86_64, cached)
    if [ ! -f "$VK_SHADERS_GEN" ]; then
        echo "  Building vulkan-shaders-gen (host x86_64)..."
        g++ -std=c++11 -O2 -o "$VK_SHADERS_GEN" \
            "$PRIMA_DIR/ggml/src/vulkan-shaders/vulkan-shaders-gen.cpp" -lpthread
    fi
    if [ ! -f "$GLSLC" ]; then
        echo "ERROR: glslc not found at $GLSLC"; exit 1
    fi

    echo "  Generating SPIR-V shaders (63 compute shaders)..."
    "$VK_SHADERS_GEN" \
        --glslc "$GLSLC" \
        --input-dir "$PRIMA_DIR/ggml/src/vulkan-shaders" \
        --target-hpp "$VK_SHADERS_HPP" \
        --target-cpp "$VK_SHADERS_CPP"

    cp "$VK_SHADERS_GEN" "$PRIMA_DIR/vulkan-shaders-gen"
    chmod +x "$PRIMA_DIR/vulkan-shaders-gen"
    sleep 1
    touch "$VK_SHADERS_CPP" "$VK_SHADERS_HPP"

    WORKER_CPPFLAGS="-I${VK_INCLUDE} ${WORKER_CPPFLAGS} -DGGML_USE_VULKAN"
    WORKER_LDFLAGS="${WORKER_LDFLAGS} -L${VK_LIB} -lvulkan"
    WORKER_MAKE_EXTRAS="GGML_VULKAN=1 GLSLC_CMD=$GLSLC"
fi

make llama-cli -j"$(nproc)" \
    CC="$CC_ANDROID" \
    CXX="$CXX_ANDROID" \
    AR="$AR_ANDROID" \
    RANLIB="$RANLIB_ANDROID" \
    UNAME_S=Linux \
    UNAME_M=android_arm64 \
    CFLAGS="-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a78 -Ofast -fno-finite-math-only -ffunction-sections -fdata-sections" \
    CXXFLAGS="-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a78 -Ofast -fno-finite-math-only -ffunction-sections -fdata-sections" \
    CPPFLAGS="${WORKER_CPPFLAGS}" \
    LDFLAGS="${WORKER_LDFLAGS} -Wl,--gc-sections -Wl,--strip-all" \
    GGML_NO_OPENMP=1 \
    $WORKER_MAKE_EXTRAS

if [ -f "$PRIMA_DIR/llama-cli" ]; then
    cp "$PRIMA_DIR/llama-cli" "$OUTPUT_DIR/prima-worker"
    echo "  prima-worker built: $OUTPUT_DIR/prima-worker"
    file "$OUTPUT_DIR/prima-worker"
else
    # Try alternate location
    FOUND=$(find "$PRIMA_DIR" -name "llama-cli" -type f 2>/dev/null | head -1)
    if [ -n "$FOUND" ]; then
        cp "$FOUND" "$OUTPUT_DIR/prima-worker"
        echo "  prima-worker built: $OUTPUT_DIR/prima-worker"
    else
        echo "ERROR: llama-cli binary not found after build"
        exit 1
    fi
fi

# ==========================================
# Stage B2: prima-worker-spec for ARM64 Android (speculative decoding)
# ==========================================
# No make clean needed — object files from Stage B are reused (same ARM64 flags).
# Only the speculative.cpp example needs compiling.
echo ""
echo ">>> Stage B2: Building prima-worker-spec (ARM64 Android, speculative decoding)..."

make llama-speculative -j"$(nproc)" \
    CC="$CC_ANDROID" \
    CXX="$CXX_ANDROID" \
    AR="$AR_ANDROID" \
    RANLIB="$RANLIB_ANDROID" \
    UNAME_S=Linux \
    UNAME_M=android_arm64 \
    CFLAGS="-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a78 -Ofast -fno-finite-math-only -ffunction-sections -fdata-sections" \
    CXXFLAGS="-march=armv8.2-a+dotprod+fp16 -mcpu=cortex-a78 -Ofast -fno-finite-math-only -ffunction-sections -fdata-sections" \
    CPPFLAGS="${WORKER_CPPFLAGS}" \
    LDFLAGS="${WORKER_LDFLAGS} -Wl,--gc-sections -Wl,--strip-all" \
    GGML_NO_OPENMP=1 \
    $WORKER_MAKE_EXTRAS

if [ -f "$PRIMA_DIR/llama-speculative" ]; then
    cp "$PRIMA_DIR/llama-speculative" "$OUTPUT_DIR/prima-worker-spec"
    echo "  prima-worker-spec built: $OUTPUT_DIR/prima-worker-spec"
    file "$OUTPUT_DIR/prima-worker-spec"
else
    FOUND=$(find "$PRIMA_DIR" -name "llama-speculative" -type f 2>/dev/null | head -1)
    if [ -n "$FOUND" ]; then
        cp "$FOUND" "$OUTPUT_DIR/prima-worker-spec"
        echo "  prima-worker-spec built: $OUTPUT_DIR/prima-worker-spec"
    else
        echo "WARNING: llama-speculative (ARM64) binary not found — skipping prima-worker-spec"
    fi
fi

# Clean prima.cpp build artifacts for host build
make clean 2>/dev/null || true
unset CC CXX AR RANLIB

# ==========================================
# Stage C: prima-host for x86_64 Linux
# ==========================================
echo ""
echo ">>> Stage C: Building prima-host (x86_64 Linux)..."

# Check if system libzmq is available
if pkg-config --exists libzmq 2>/dev/null; then
    echo "  Using system libzmq"
    ZMQ_CFLAGS=$(pkg-config --cflags libzmq)
    ZMQ_LDFLAGS=$(pkg-config --libs libzmq)
else
    echo "  Building libzmq from vendor for host..."
    ZMQ_BUILD_HOST="$BUILD_BASE/libzmq-host"
    ZMQ_INSTALL_HOST="$BUILD_BASE/libzmq-host-install"
    if [ ! -f "$ZMQ_INSTALL_HOST/lib/libzmq.a" ]; then
        rm -rf "$ZMQ_BUILD_HOST"
        mkdir -p "$ZMQ_BUILD_HOST"
        cd "$ZMQ_BUILD_HOST"
        cmake "$LIBZMQ_DIR" \
            -DCMAKE_INSTALL_PREFIX="$ZMQ_INSTALL_HOST" \
            -DBUILD_SHARED_LIBS=OFF \
            -DBUILD_STATIC=ON \
            -DWITH_LIBSODIUM=OFF \
            -DWITH_TLS=OFF \
            -DZMQ_BUILD_TESTS=OFF \
            -DWITH_DOCS=OFF \
            -DWITH_PERF_TOOL=OFF \
            -DCMAKE_BUILD_TYPE=Release
        cmake --build . -j"$(nproc)"
        cmake --install .
    fi
    ZMQ_CFLAGS="-I${ZMQ_INSTALL_HOST}/include"
    ZMQ_LDFLAGS="-L${ZMQ_INSTALL_HOST}/lib -lzmq"
fi

# Build HiGHS from vendor
HIGHS_INSTALL="$BUILD_BASE/highs-install"
if [ ! -f "$HIGHS_INSTALL/lib/libhighs.a" ] && [ ! -f "$HIGHS_INSTALL/lib/libhighs.so" ]; then
    echo "  Building HiGHS from vendor..."
    HIGHS_BUILD="$BUILD_BASE/highs-build"
    rm -rf "$HIGHS_BUILD"
    mkdir -p "$HIGHS_BUILD"
    cd "$HIGHS_BUILD"
    cmake "$HIGHS_DIR" \
        -DCMAKE_INSTALL_PREFIX="$HIGHS_INSTALL" \
        -DBUILD_SHARED_LIBS=OFF \
        -DCMAKE_BUILD_TYPE=Release
    cmake --build . -j"$(nproc)"
    cmake --install .
    echo "  HiGHS installed to $HIGHS_INSTALL"
else
    echo "  HiGHS already built, skipping."
fi

HIGHS_CFLAGS="-I${HIGHS_INSTALL}/include/highs"
HIGHS_LDFLAGS="-L${HIGHS_INSTALL}/lib -lhighs"

# Build prima-host (llama-cli with HiGHS for rank 0)
cd "$PRIMA_DIR"
make clean 2>/dev/null || true

HOST_MAKE_FLAGS=(
    CPPFLAGS="-DPRIMA_DEBUG ${ZMQ_CFLAGS} -I${CPPZMQ_DIR} ${HIGHS_CFLAGS} -DUSE_HIGHS"
    LDFLAGS="${ZMQ_LDFLAGS} ${HIGHS_LDFLAGS} -lz"
    USE_HIGHS=1
)

make llama-cli -j"$(nproc)" "${HOST_MAKE_FLAGS[@]}"

if [ -f "$PRIMA_DIR/llama-cli" ]; then
    cp "$PRIMA_DIR/llama-cli" "$OUTPUT_DIR/prima-host"
    echo "  prima-host built: $OUTPUT_DIR/prima-host"
    file "$OUTPUT_DIR/prima-host"
else
    FOUND=$(find "$PRIMA_DIR" -name "llama-cli" -type f 2>/dev/null | head -1)
    if [ -n "$FOUND" ]; then
        cp "$FOUND" "$OUTPUT_DIR/prima-host"
        echo "  prima-host built: $OUTPUT_DIR/prima-host"
    else
        echo "ERROR: llama-cli (host) binary not found after build"
        exit 1
    fi
fi

# ==========================================
# Stage D: prima-host-spec (speculative decoding host)
# ==========================================
echo ""
echo ">>> Stage D: Building prima-host-spec (x86_64 Linux, speculative decoding)..."

# llama-speculative shares the same flags as llama-cli but builds examples/speculative/speculative.cpp
# No need for make clean — object files from Stage C are reused (same flags)
make llama-speculative -j"$(nproc)" "${HOST_MAKE_FLAGS[@]}"

if [ -f "$PRIMA_DIR/llama-speculative" ]; then
    cp "$PRIMA_DIR/llama-speculative" "$OUTPUT_DIR/prima-host-spec"
    echo "  prima-host-spec built: $OUTPUT_DIR/prima-host-spec"
    file "$OUTPUT_DIR/prima-host-spec"
else
    FOUND=$(find "$PRIMA_DIR" -name "llama-speculative" -type f 2>/dev/null | head -1)
    if [ -n "$FOUND" ]; then
        cp "$FOUND" "$OUTPUT_DIR/prima-host-spec"
        echo "  prima-host-spec built: $OUTPUT_DIR/prima-host-spec"
    else
        echo "WARNING: llama-speculative binary not found — skipping prima-host-spec"
    fi
fi

make clean 2>/dev/null || true

echo ""
echo "============================================"
echo " BUILD COMPLETE"
echo "============================================"
echo "  ARM64 worker:       $OUTPUT_DIR/prima-worker"
echo "  ARM64 worker-spec:  $OUTPUT_DIR/prima-worker-spec"
echo "  x86_64 host:        $OUTPUT_DIR/prima-host"
echo "  x86_64 host-spec:   $OUTPUT_DIR/prima-host-spec"
ls -lh "$OUTPUT_DIR/prima-worker" "$OUTPUT_DIR/prima-worker-spec" \
       "$OUTPUT_DIR/prima-host" "$OUTPUT_DIR/prima-host-spec" 2>/dev/null || true
echo "============================================"
