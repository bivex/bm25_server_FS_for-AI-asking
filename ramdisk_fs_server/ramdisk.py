from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import RamDiskInfo


def _run_command(*args: str) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _extract_device(stdout: str) -> str:
    for line in stdout.splitlines():
        for token in line.split():
            if token.startswith("/dev/disk"):
                return token
    raise ValueError(f"Could not find disk device in output: {stdout!r}")


@dataclass(slots=True)
class RamDiskManager:
    current: RamDiskInfo | None = None

    def create(self, size_mb: int, label: str = "SQLFSRAM", fs_type: str = "HFS+") -> RamDiskInfo:
        if platform.system() != "Darwin":
            raise RuntimeError("RAM disk creation is supported only on macOS")
        if self.current is not None:
            raise RuntimeError("RAM disk is already mounted")
        if size_mb <= 0:
            raise ValueError("size_mb must be positive")

        sectors = size_mb * 2048
        device_stdout = _run_command("hdiutil", "attach", "-nomount", f"ram://{sectors}")
        device = _extract_device(device_stdout)
        try:
            _run_command("diskutil", "erasevolume", fs_type, label, device)
        except Exception:
            subprocess.run(["hdiutil", "detach", device, "-force"], capture_output=True, text=True)
            raise

        info = RamDiskInfo(
            device=device,
            mount_point=str(Path("/Volumes") / label),
            label=label,
            size_mb=size_mb,
            fs_type=fs_type,
            sectors=sectors,
        )
        self.current = info
        return info

    def destroy(self) -> None:
        if self.current is None:
            return
        subprocess.run(["hdiutil", "detach", self.current.device, "-force"], capture_output=True, text=True, check=True)
        self.current = None
