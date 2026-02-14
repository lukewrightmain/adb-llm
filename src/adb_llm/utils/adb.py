"""Low-level async ADB subprocess wrapper.

All ADB interaction funnels through this module.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass

from loguru import logger

from adb_llm.core.errors import AdbError

# Locate adb binary once at import time
ADB_BIN = shutil.which("adb") or "adb"


@dataclass
class AdbDevice:
    """Raw device entry from `adb devices -l`."""
    serial: str
    state: str  # "device", "offline", "unauthorized", etc.
    usb: str = ""
    product: str = ""
    model: str = ""
    transport_id: str = ""


async def _run(
    *args: str,
    timeout: float = 30,
    check: bool = True,
) -> tuple[str, str, int]:
    """Run an ADB command, return (stdout, stderr, returncode)."""
    logger.debug("adb {}", " ".join(args))
    proc = await asyncio.create_subprocess_exec(
        ADB_BIN, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise AdbError(" ".join(args), -1, "timed out")

    stdout = stdout_bytes.decode(errors="replace").strip()
    stderr = stderr_bytes.decode(errors="replace").strip()
    rc = proc.returncode or 0

    if check and rc != 0:
        raise AdbError(" ".join(args), rc, stderr)
    return stdout, stderr, rc


async def adb_devices() -> list[AdbDevice]:
    """List connected devices via `adb devices -l`."""
    stdout, _, _ = await _run("devices", "-l")
    devices: list[AdbDevice] = []
    for line in stdout.splitlines()[1:]:  # skip header
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        state = parts[1]
        # Parse key:value pairs
        attrs: dict[str, str] = {}
        for part in parts[2:]:
            if ":" in part:
                k, v = part.split(":", 1)
                attrs[k] = v
        devices.append(AdbDevice(
            serial=serial,
            state=state,
            usb=attrs.get("usb", ""),
            product=attrs.get("product", ""),
            model=attrs.get("model", ""),
            transport_id=attrs.get("transport_id", ""),
        ))
    return devices


async def adb_shell(serial: str, cmd: str, timeout: float = 30, check: bool = True) -> str:
    """Run a shell command on a device, return stdout."""
    stdout, stderr, rc = await _run("-s", serial, "shell", cmd, timeout=timeout, check=False)
    if check and rc != 0:
        raise AdbError(f"-s {serial} shell {cmd}", rc, stderr)
    return stdout


async def adb_push(
    serial: str,
    local_path: str,
    remote_path: str,
    timeout: float = 600,
) -> tuple[float, float]:
    """Push a file to a device. Returns (elapsed_seconds, bytes_per_second)."""
    import os
    file_size = os.path.getsize(local_path)

    t0 = time.monotonic()
    await _run("-s", serial, "push", local_path, remote_path, timeout=timeout)
    elapsed = time.monotonic() - t0

    speed = file_size / elapsed if elapsed > 0 else 0
    logger.info(
        "Pushed {} to {} in {:.1f}s ({:.1f} MB/s)",
        local_path, serial, elapsed, speed / 1e6,
    )
    return elapsed, speed


async def adb_pull(
    serial: str,
    remote_path: str,
    local_path: str,
    timeout: float = 600,
) -> None:
    """Pull a file from a device."""
    await _run("-s", serial, "pull", remote_path, local_path, timeout=timeout)


async def adb_forward(serial: str, local_port: int, remote_port: int) -> None:
    """Create a TCP port forward over USB: localhost:local_port -> device:remote_port."""
    await _run("-s", serial, "forward", f"tcp:{local_port}", f"tcp:{remote_port}")
    logger.info("Forward {}:{} -> {}:{}", serial, local_port, serial, remote_port)


async def adb_forward_remove(serial: str, local_port: int) -> None:
    """Remove a specific port forward."""
    await _run("-s", serial, "forward", "--remove", f"tcp:{local_port}", check=False)


async def adb_forward_remove_all(serial: str) -> None:
    """Remove all port forwards for a device."""
    await _run("-s", serial, "forward", "--remove-all", check=False)


async def adb_forward_list(serial: str | None = None) -> list[tuple[str, int, int]]:
    """List active forwards. Returns [(serial, local_port, remote_port), ...]."""
    args = ["forward", "--list"]
    if serial:
        args = ["-s", serial] + args
    stdout, _, _ = await _run(*args, check=False)
    result: list[tuple[str, int, int]] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            s = parts[0]
            lp = int(parts[1].replace("tcp:", ""))
            rp = int(parts[2].replace("tcp:", ""))
            result.append((s, lp, rp))
    return result
