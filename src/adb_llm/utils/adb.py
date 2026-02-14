"""Low-level async ADB wrapper that runs commands via SSH on a remote host.

ADB devices are physically connected to a Windows PC. This module SSHes
into that machine to run ADB commands, and also handles file transfers
(SCP to Windows, then adb push from Windows to phone).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from loguru import logger

from adb_llm.core.errors import AdbError

# Remote host running ADB (Windows PC with phones connected via USB)
# Can be overridden via ADB_LLM_SSH_HOST env var
SSH_HOST = os.environ.get("ADB_LLM_SSH_HOST", "winpc")
ADB_BIN = os.environ.get("ADB_LLM_ADB_BIN", r'"C:\Program Files\platform-tools\adb.exe"')

# Windows temp directory for staging model files before adb push
WIN_STAGING_DIR = os.environ.get("ADB_LLM_WIN_STAGING", r"C:\Users\Lukio-4090\adb-llm-staging")


@dataclass
class AdbDevice:
    """Raw device entry from `adb devices -l`."""
    serial: str
    state: str  # "device", "offline", "unauthorized", etc.
    usb: str = ""
    product: str = ""
    model: str = ""
    transport_id: str = ""


async def _ssh_run(
    cmd: str,
    timeout: float = 30,
    check: bool = True,
) -> tuple[str, str, int]:
    """Run a command on the Windows PC via SSH."""
    logger.debug("ssh {} -> {}", SSH_HOST, cmd)
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "ConnectTimeout=10", SSH_HOST, cmd,
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
        raise AdbError(cmd, -1, "SSH command timed out")

    stdout = stdout_bytes.decode(errors="replace").strip()
    stderr = stderr_bytes.decode(errors="replace").strip()
    rc = proc.returncode or 0

    if check and rc != 0:
        raise AdbError(cmd, rc, stderr or stdout)
    return stdout, stderr, rc


async def _adb_run(
    *args: str,
    timeout: float = 30,
    check: bool = True,
) -> tuple[str, str, int]:
    """Run an ADB command on the remote Windows PC via SSH."""
    adb_cmd = f'{ADB_BIN} {" ".join(args)}'
    return await _ssh_run(adb_cmd, timeout=timeout, check=check)


async def adb_devices() -> list[AdbDevice]:
    """List connected devices via `adb devices -l` on the Windows PC."""
    stdout, _, _ = await _adb_run("devices", "-l")
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
    """Run a shell command on a device via the remote ADB host."""
    stdout, stderr, rc = await _adb_run(
        "-s", serial, "shell", f'"{cmd}"',
        timeout=timeout, check=False,
    )
    if check and rc != 0:
        raise AdbError(f"adb -s {serial} shell {cmd}", rc, stderr)
    return stdout


async def adb_push(
    serial: str,
    local_path: str,
    remote_path: str,
    timeout: float = 600,
) -> tuple[float, float]:
    """Push a file to a device.

    Flow: SCP file from this server -> Windows PC staging dir,
    then adb push from Windows -> phone.

    Returns (elapsed_seconds, bytes_per_second).
    """
    file_size = os.path.getsize(local_path)
    filename = os.path.basename(local_path)
    win_staged = f"{WIN_STAGING_DIR}\\{filename}"

    # Ensure staging dir exists on Windows
    await _ssh_run(f'mkdir "{WIN_STAGING_DIR}" 2>nul & echo ok', check=False)

    t0 = time.monotonic()

    # Step 1: SCP file to Windows PC
    logger.info("SCP {} -> {}:{}", local_path, SSH_HOST, win_staged)
    scp_proc = await asyncio.create_subprocess_exec(
        "scp", "-o", "ConnectTimeout=10",
        local_path, f"{SSH_HOST}:{win_staged}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        scp_out, scp_err = await asyncio.wait_for(scp_proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        scp_proc.kill()
        await scp_proc.wait()
        raise AdbError(f"scp {local_path}", -1, "SCP timed out")

    if scp_proc.returncode != 0:
        raise AdbError(
            f"scp to {SSH_HOST}",
            scp_proc.returncode or 1,
            scp_err.decode(errors="replace"),
        )

    # Step 2: ADB push from Windows to phone
    logger.info("ADB push {} -> {}:{}", win_staged, serial, remote_path)
    await _adb_run(
        "-s", serial, "push", f'"{win_staged}"', remote_path,
        timeout=timeout,
    )

    elapsed = time.monotonic() - t0
    speed = file_size / elapsed if elapsed > 0 else 0

    # Clean up staged file on Windows
    await _ssh_run(f'del "{win_staged}" 2>nul', check=False)

    logger.info(
        "Pushed {} to {} in {:.1f}s ({:.1f} MB/s)",
        local_path, serial, elapsed, speed / 1e6,
    )
    return elapsed, speed


async def adb_push_local_to_device(
    serial: str,
    win_local_path: str,
    remote_path: str,
    timeout: float = 600,
) -> tuple[float, float]:
    """Push a file already on the Windows PC to a device.

    Use this when the model file is already on the Windows machine
    (skips the SCP step).
    """
    t0 = time.monotonic()
    await _adb_run(
        "-s", serial, "push", f'"{win_local_path}"', remote_path,
        timeout=timeout,
    )
    elapsed = time.monotonic() - t0

    # Get file size from Windows
    stdout, _, _ = await _ssh_run(
        f'powershell -Command "(Get-Item \'{win_local_path}\').Length"',
        check=False,
    )
    try:
        file_size = int(stdout.strip())
    except ValueError:
        file_size = 0

    speed = file_size / elapsed if elapsed > 0 and file_size > 0 else 0
    logger.info(
        "Pushed {} to {} in {:.1f}s ({:.1f} MB/s)",
        win_local_path, serial, elapsed, speed / 1e6,
    )
    return elapsed, speed


async def adb_pull(
    serial: str,
    remote_path: str,
    local_path: str,
    timeout: float = 600,
) -> None:
    """Pull a file from a device (to the Windows PC, then SCP here)."""
    filename = os.path.basename(remote_path)
    win_staged = f"{WIN_STAGING_DIR}\\{filename}"

    await _ssh_run(f'mkdir "{WIN_STAGING_DIR}" 2>nul & echo ok', check=False)
    await _adb_run("-s", serial, "pull", remote_path, f'"{win_staged}"', timeout=timeout)

    # SCP from Windows to here
    scp_proc = await asyncio.create_subprocess_exec(
        "scp", "-o", "ConnectTimeout=10",
        f"{SSH_HOST}:{win_staged}", local_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(scp_proc.communicate(), timeout=timeout)
    await _ssh_run(f'del "{win_staged}" 2>nul', check=False)


async def adb_forward(serial: str, local_port: int, remote_port: int) -> None:
    """Create a TCP port forward on the Windows PC.

    Note: The forward is on the Windows PC's localhost, not this server.
    For inference, we also need an SSH tunnel from this server to the
    Windows PC's forwarded port.
    """
    await _adb_run("-s", serial, "forward", f"tcp:{local_port}", f"tcp:{remote_port}")
    logger.info("Forward {}:{} -> {}:{}", serial, local_port, serial, remote_port)


async def adb_forward_remove(serial: str, local_port: int) -> None:
    """Remove a specific port forward on the Windows PC."""
    await _adb_run("-s", serial, "forward", "--remove", f"tcp:{local_port}", check=False)


async def adb_forward_remove_all(serial: str) -> None:
    """Remove all port forwards for a device on the Windows PC."""
    await _adb_run("-s", serial, "forward", "--remove-all", check=False)


async def adb_forward_list(serial: str | None = None) -> list[tuple[str, int, int]]:
    """List active forwards on the Windows PC."""
    args = ["forward", "--list"]
    if serial:
        args = ["-s", serial] + args
    stdout, _, _ = await _adb_run(*args, check=False)
    result: list[tuple[str, int, int]] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            s = parts[0]
            lp = int(parts[1].replace("tcp:", ""))
            rp = int(parts[2].replace("tcp:", ""))
            result.append((s, lp, rp))
    return result


async def ssh_tunnel(
    local_port: int,
    remote_port: int,
    background: bool = True,
) -> asyncio.subprocess.Process | None:
    """Create an SSH tunnel: this server:local_port -> Windows PC:remote_port.

    Used to make ADB-forwarded ports on Windows accessible from this server.
    """
    args = [
        "ssh", "-o", "ConnectTimeout=10",
        "-N",  # no remote command
        "-L", f"{local_port}:127.0.0.1:{remote_port}",
        SSH_HOST,
    ]
    if background:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        # Give it a moment to establish
        await asyncio.sleep(1)
        if proc.returncode is not None:
            stderr = (await proc.stderr.read()).decode(errors="replace")
            raise AdbError(f"SSH tunnel :{local_port}->:{remote_port}", proc.returncode, stderr)
        logger.info("SSH tunnel: localhost:{} -> {}:{}", local_port, SSH_HOST, remote_port)
        return proc
    return None
