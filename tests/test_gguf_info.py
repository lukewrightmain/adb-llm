"""Tests for GGUF header parsing."""

import struct
from pathlib import Path

import pytest

from adb_llm.model.gguf_info import GGUF_MAGIC, GGUFInfo, read_gguf_info


def test_read_minimal_gguf(tmp_gguf: Path):
    """Test reading a minimal GGUF file."""
    info = read_gguf_info(tmp_gguf)
    assert info.version == 3
    assert info.tensor_count == 42
    assert info.file_size_bytes > 0


def test_invalid_magic(tmp_path: Path):
    """Test rejection of non-GGUF files."""
    bad = tmp_path / "not-gguf.bin"
    bad.write_bytes(b"NOT_GGUF_DATA_HERE")
    with pytest.raises(ValueError, match="Not a GGUF"):
        read_gguf_info(bad)


def test_gguf_with_metadata(tmp_path: Path):
    """Test reading GGUF with metadata KV pairs."""
    path = tmp_path / "model.gguf"
    with open(path, "wb") as f:
        # Header
        f.write(struct.pack("<I", GGUF_MAGIC))
        f.write(struct.pack("<I", 3))  # version
        f.write(struct.pack("<Q", 10))  # tensors
        f.write(struct.pack("<Q", 1))   # 1 KV pair

        # KV: general.architecture = "llama"
        key = b"general.architecture"
        f.write(struct.pack("<Q", len(key)))
        f.write(key)
        f.write(struct.pack("<I", 8))  # STRING type
        value = b"llama"
        f.write(struct.pack("<Q", len(value)))
        f.write(value)

        f.write(b"\x00" * 256)

    info = read_gguf_info(path)
    assert info.architecture == "llama"
    assert info.tensor_count == 10


def test_display_name():
    info = GGUFInfo(
        path=Path("model.gguf"),
        file_size_bytes=1024 * 1024 * 1024,
        model_name="DeepSeek 33B",
        quantization="Q4_K_M",
    )
    assert info.display_name == "DeepSeek 33B Q4_K_M"
    assert info.file_size_gb == pytest.approx(1.0, rel=0.01)
