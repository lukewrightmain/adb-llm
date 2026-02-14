"""GGUF file header and metadata parser.

Reads just enough of the GGUF header to extract model name, parameter count,
quantization type, and architecture without loading the full file.

Spec: https://github.com/ggerganov/ggml/blob/master/docs/gguf.md
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

GGUF_MAGIC = 0x46554747  # "GGUF" in little-endian


@dataclass
class GGUFInfo:
    """Metadata extracted from a GGUF file header."""
    path: Path
    file_size_bytes: int
    version: int = 0
    tensor_count: int = 0
    metadata_kv_count: int = 0
    architecture: str = ""
    model_name: str = ""
    quantization: str = ""
    context_length: int = 0
    parameters: int = 0

    @property
    def file_size_mb(self) -> int:
        return self.file_size_bytes // (1024 * 1024)

    @property
    def file_size_gb(self) -> float:
        return self.file_size_bytes / (1024 ** 3)

    @property
    def display_name(self) -> str:
        parts = []
        if self.model_name:
            parts.append(self.model_name)
        elif self.architecture:
            parts.append(self.architecture)
        else:
            parts.append(self.path.stem)
        if self.quantization:
            parts.append(self.quantization)
        return " ".join(parts)


def read_gguf_info(path: str | Path) -> GGUFInfo:
    """Read GGUF metadata from file header.

    Only reads the first few KB - does not load tensors.
    """
    path = Path(path)
    info = GGUFInfo(path=path, file_size_bytes=path.stat().st_size)

    with open(path, "rb") as f:
        # Magic number (4 bytes)
        magic = struct.unpack("<I", f.read(4))[0]
        if magic != GGUF_MAGIC:
            raise ValueError(f"Not a GGUF file (magic: 0x{magic:08X})")

        # Version (4 bytes)
        info.version = struct.unpack("<I", f.read(4))[0]
        if info.version not in (2, 3):
            raise ValueError(f"Unsupported GGUF version: {info.version}")

        # Tensor count, metadata KV count (each 8 bytes in v3, 4 bytes in v2)
        if info.version >= 3:
            info.tensor_count = struct.unpack("<Q", f.read(8))[0]
            info.metadata_kv_count = struct.unpack("<Q", f.read(8))[0]
        else:
            info.tensor_count = struct.unpack("<I", f.read(4))[0]
            info.metadata_kv_count = struct.unpack("<I", f.read(4))[0]

        # Parse metadata key-value pairs (just the ones we care about)
        for _ in range(min(info.metadata_kv_count, 200)):  # cap iterations
            try:
                key = _read_string(f, info.version)
                value_type = struct.unpack("<I", f.read(4))[0]
                value = _read_value(f, value_type, info.version)
            except (struct.error, EOFError, UnicodeDecodeError):
                break  # Reached unreadable data

            if key == "general.architecture":
                info.architecture = str(value)
            elif key == "general.name":
                info.model_name = str(value)
            elif key == "general.file_type":
                info.quantization = _file_type_to_quant(value)
            elif key.endswith(".context_length"):
                info.context_length = int(value)

    return info


def _read_string(f, version: int) -> str:
    """Read a GGUF string (length-prefixed)."""
    if version >= 3:
        length = struct.unpack("<Q", f.read(8))[0]
    else:
        length = struct.unpack("<I", f.read(4))[0]
    if length > 65536:
        raise ValueError(f"String too long: {length}")
    return f.read(length).decode("utf-8")


# GGUF value type IDs
_GGUF_TYPE_UINT8 = 0
_GGUF_TYPE_INT8 = 1
_GGUF_TYPE_UINT16 = 2
_GGUF_TYPE_INT16 = 3
_GGUF_TYPE_UINT32 = 4
_GGUF_TYPE_INT32 = 5
_GGUF_TYPE_FLOAT32 = 6
_GGUF_TYPE_BOOL = 7
_GGUF_TYPE_STRING = 8
_GGUF_TYPE_ARRAY = 9
_GGUF_TYPE_UINT64 = 10
_GGUF_TYPE_INT64 = 11
_GGUF_TYPE_FLOAT64 = 12

_TYPE_SIZES = {
    _GGUF_TYPE_UINT8: ("<B", 1),
    _GGUF_TYPE_INT8: ("<b", 1),
    _GGUF_TYPE_UINT16: ("<H", 2),
    _GGUF_TYPE_INT16: ("<h", 2),
    _GGUF_TYPE_UINT32: ("<I", 4),
    _GGUF_TYPE_INT32: ("<i", 4),
    _GGUF_TYPE_FLOAT32: ("<f", 4),
    _GGUF_TYPE_BOOL: ("<B", 1),
    _GGUF_TYPE_UINT64: ("<Q", 8),
    _GGUF_TYPE_INT64: ("<q", 8),
    _GGUF_TYPE_FLOAT64: ("<d", 8),
}


def _read_value(f, value_type: int, version: int):
    """Read a GGUF typed value."""
    if value_type == _GGUF_TYPE_STRING:
        return _read_string(f, version)
    if value_type == _GGUF_TYPE_ARRAY:
        elem_type = struct.unpack("<I", f.read(4))[0]
        if version >= 3:
            count = struct.unpack("<Q", f.read(8))[0]
        else:
            count = struct.unpack("<I", f.read(4))[0]
        # Skip array contents - we don't need them
        if elem_type in _TYPE_SIZES:
            _, size = _TYPE_SIZES[elem_type]
            f.read(size * count)
        elif elem_type == _GGUF_TYPE_STRING:
            for _ in range(min(count, 1000)):
                _read_string(f, version)
        return f"[array of {count}]"
    if value_type in _TYPE_SIZES:
        fmt, size = _TYPE_SIZES[value_type]
        return struct.unpack(fmt, f.read(size))[0]
    raise ValueError(f"Unknown GGUF value type: {value_type}")


# Map GGUF file_type enum to quantization name
_FILE_TYPE_MAP = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1",
    7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
    11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
    14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S",
    17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS",
    20: "IQ2_XS", 21: "IQ3_XXS", 22: "IQ1_S",
    23: "IQ4_NL", 24: "IQ3_S", 25: "IQ2_S",
    26: "IQ4_XS", 27: "IQ1_M", 28: "BF16",
}


def _file_type_to_quant(value) -> str:
    try:
        return _FILE_TYPE_MAP.get(int(value), f"type_{value}")
    except (ValueError, TypeError):
        return str(value)
