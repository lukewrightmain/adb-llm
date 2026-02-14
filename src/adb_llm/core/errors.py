"""Exception hierarchy for adb-llm."""


class AdbLlmError(Exception):
    """Base exception for all adb-llm errors."""


class AdbError(AdbLlmError):
    """ADB command failed."""

    def __init__(self, command: str, returncode: int, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"adb command failed (rc={returncode}): {command}\n{stderr}")


class DeviceNotFoundError(AdbLlmError):
    """Requested device serial not found."""

    def __init__(self, serial: str):
        self.serial = serial
        super().__init__(f"Device not found: {serial}")


class DeviceOfflineError(AdbLlmError):
    """Device is connected but offline."""

    def __init__(self, serial: str):
        self.serial = serial
        super().__init__(f"Device offline: {serial}")


class ModelNotFoundError(AdbLlmError):
    """Model file does not exist."""

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Model not found: {path}")


class InsufficientStorageError(AdbLlmError):
    """Device doesn't have enough free storage."""

    def __init__(self, serial: str, required_mb: int, available_mb: int):
        self.serial = serial
        self.required_mb = required_mb
        self.available_mb = available_mb
        super().__init__(
            f"Device {serial}: need {required_mb}MB, have {available_mb}MB free"
        )


class TunnelError(AdbLlmError):
    """Failed to create or maintain ADB tunnel."""

    def __init__(self, serial: str, message: str):
        self.serial = serial
        super().__init__(f"Tunnel error ({serial}): {message}")


class RpcServerError(AdbLlmError):
    """RPC server failed to start or crashed."""

    def __init__(self, serial: str, message: str):
        self.serial = serial
        super().__init__(f"RPC server error ({serial}): {message}")


class InferenceError(AdbLlmError):
    """Inference pipeline error."""
