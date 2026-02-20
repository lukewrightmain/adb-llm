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

from cellswarm.core.errors import AdbError

# Remote host running ADB (Windows PC with phones connected via USB)
# Can be overridden via CELLSWARM_SSH_HOST env var
SSH_HOST = os.environ.get("CELLSWARM_SSH_HOST", "winpc")
ADB_BIN = os.environ.get("CELLSWARM_ADB_BIN", r'"C:\Program Files\platform-tools\adb.exe"')

# Windows temp directory for staging model files before adb push
WIN_STAGING_DIR = os.environ.get("CELLSWARM_WIN_STAGING", r"C:\Users\Lukio-4090\cellswarm-staging")


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
        "ssh",
        "-o", "ConnectTimeout=10",
        "-o", "Compression=no",
        "-o", f"ControlPath=/tmp/ssh-cellswarm-%r@%h:%p",
        "-o", "ControlMaster=auto",
        "-o", "ControlPersist=600",
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


# --- Reverse tunnels (host -> phone) for cellswarm ring topology ---


async def adb_reverse(serial: str, remote_port: int, local_port: int) -> None:
    """Create an ADB reverse tunnel on the Windows PC.

    Makes the phone's localhost:remote_port route to Windows PC's localhost:local_port.
    Combined with an SSH reverse tunnel, this lets the phone reach this server.
    """
    await _adb_run("-s", serial, "reverse", f"tcp:{remote_port}", f"tcp:{local_port}")
    logger.info("Reverse {}:phone:{} -> winpc:{}", serial, remote_port, local_port)


async def adb_reverse_remove(serial: str, remote_port: int) -> None:
    """Remove a specific reverse tunnel on the Windows PC."""
    await _adb_run("-s", serial, "reverse", "--remove", f"tcp:{remote_port}", check=False)


async def adb_reverse_remove_all(serial: str) -> None:
    """Remove all reverse tunnels for a device on the Windows PC."""
    await _adb_run("-s", serial, "reverse", "--remove-all", check=False)


async def adb_reverse_list(serial: str | None = None) -> list[tuple[str, int, int]]:
    """List active reverse tunnels on the Windows PC.

    Returns [(serial, remote_port_on_phone, local_port_on_winpc), ...].
    """
    args = ["reverse", "--list"]
    if serial:
        args = ["-s", serial] + args
    stdout, _, _ = await _adb_run(*args, check=False)
    result: list[tuple[str, int, int]] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            s = parts[0]
            rp = int(parts[1].replace("tcp:", ""))
            lp = int(parts[2].replace("tcp:", ""))
            result.append((s, rp, lp))
    return result


async def ssh_reverse_tunnel(
    local_port: int,
    remote_port: int,
) -> asyncio.subprocess.Process:
    """Create an SSH reverse tunnel: Windows PC:remote_port -> this server:local_port.

    Used to make this server's ports accessible from the Windows PC side,
    so that adb reverse tunnels can route phone traffic back here.
    """
    args = [
        "ssh",
        "-o", "ConnectTimeout=10",
        "-o", "Compression=no",
        "-o", f"ControlPath=/tmp/ssh-cellswarm-%r@%h:%p",
        "-o", "ControlMaster=auto",
        "-o", "ControlPersist=600",
        "-N",
        "-R", f"{remote_port}:127.0.0.1:{local_port}",
        SSH_HOST,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(1)
    if proc.returncode is not None:
        stderr = (await proc.stderr.read()).decode(errors="replace")
        raise AdbError(
            f"SSH reverse tunnel winpc:{remote_port}->localhost:{local_port}",
            proc.returncode,
            stderr,
        )
    logger.info(
        "SSH reverse tunnel: {}:{} -> localhost:{}",
        SSH_HOST, remote_port, local_port,
    )
    return proc


# --- USB Tethering (RNDIS/NCM) for direct TCP/IP over USB ---


async def enable_usb_tethering(
    serial: str,
    mode: str = "rndis",
) -> bool:
    """Enable USB tethering (RNDIS or NCM) on a phone while keeping ADB.

    Sets USB functions to rndis,adb (or ncm,adb) which creates a USB
    network interface on the WinPC. The phone gets IP 192.168.42.1 by
    default (Android's built-in RNDIS/NCM IP).

    Args:
        serial: Device serial number.
        mode: "rndis" or "ncm" (NCM preferred on Android 14+).

    Returns:
        True if the function switch succeeded.
    """
    func = f"{mode},adb"
    logger.info("Enabling USB tethering on {} (mode={})", serial[:8], mode)

    try:
        await adb_shell(serial, f"svc usb setFunctions {func}", timeout=15)
    except Exception as e:
        logger.warning("USB function switch failed on {}: {}", serial[:8], e)
        return False

    # Wait for USB re-enumeration (function switch causes brief disconnect)
    await asyncio.sleep(5)

    # Verify ADB is still connected
    try:
        out = await adb_shell(serial, "getprop sys.usb.state", timeout=10)
        if mode in out:
            logger.info("USB tethering active on {}: {}", serial[:8], out.strip())
            return True
        else:
            logger.warning("USB state on {} is '{}', expected '{}'", serial[:8], out.strip(), func)
            return False
    except Exception as e:
        logger.error("ADB lost after tethering switch on {}: {}", serial[:8], e)
        return False


async def disable_usb_tethering(serial: str) -> None:
    """Reset USB functions back to ADB-only (mtp,adb default)."""
    try:
        await adb_shell(serial, "svc usb setFunctions mtp,adb", timeout=15)
        await asyncio.sleep(3)
        logger.info("USB tethering disabled on {}", serial[:8])
    except Exception:
        logger.warning("Failed to reset USB functions on {}", serial[:8])


async def get_tether_ip(serial: str) -> str | None:
    """Get the phone's IP on the tethering interface (rndis0 or ncm0).

    Returns the IP address string, or None if tethering is not active.
    """
    for iface in ("rndis0", "ncm0", "usb0"):
        try:
            out = await adb_shell(
                serial,
                f"ip addr show {iface} 2>/dev/null | grep 'inet ' | awk '{{print $2}}' | cut -d/ -f1",
                timeout=10,
            )
            ip = out.strip()
            if ip and ip != "":
                logger.debug("Tether IP for {} on {}: {}", serial[:8], iface, ip)
                return ip
        except Exception:
            continue
    return None


async def set_tether_ip(serial: str, ip: str, iface: str = "rndis0") -> bool:
    """Set a static IP on the phone's tethering interface.

    Args:
        serial: Device serial number.
        ip: IP address to assign (e.g., "10.0.0.1").
        iface: Network interface name.

    Returns:
        True if successful.
    """
    try:
        # Bring interface up and assign IP
        await adb_shell(serial, f"ip link set {iface} up", timeout=10)
        await adb_shell(serial, f"ip addr flush dev {iface}", timeout=10)
        await adb_shell(serial, f"ip addr add {ip}/24 dev {iface}", timeout=10)
        logger.info("Set tether IP {} on {} ({})", ip, serial[:8], iface)
        return True
    except Exception as e:
        logger.error("Failed to set tether IP on {}: {}", serial[:8], e)
        return False


async def open_tether_firewall(serial: str, port_range: str = "9000:10100", iface: str = "rndis0") -> None:
    """Open firewall for incoming connections on the tethering interface.

    Uses iptables to allow TCP connections on the ZMQ port range.
    """
    try:
        await adb_shell(
            serial,
            f"iptables -I INPUT -i {iface} -p tcp --dport {port_range} -j ACCEPT",
            timeout=10,
            check=False,
        )
        logger.info("Firewall opened on {} for {} ports {}", serial[:8], iface, port_range)
    except Exception as e:
        logger.warning("Firewall rule failed on {} (may need root): {}", serial[:8], e)
