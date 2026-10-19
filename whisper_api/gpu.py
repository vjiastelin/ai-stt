"""Host GPU and NVIDIA driver facts for the startup log.

A model that fails to load on CUDA (e.g. "forward compatibility was attempted on
non supported HW") is usually a host/driver mismatch, and the log is often the
only thing left of a rented GPU box, so say what we ran on before loading.
"""
import re
from pathlib import Path

PROC_NVIDIA = Path("/proc/driver/nvidia")

# "NVRM version: NVIDIA UNIX x86_64 Kernel Module  535.113.01  Tue Sep 12 ..."
# "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  570.211.01  Release Build ..."
_DRIVER_RE = re.compile(r"Kernel Module(?: for \S+)?\s+(\d+(?:\.\d+)+)")


def describe_gpu(proc: Path = PROC_NVIDIA) -> str:
    """'NVIDIA GeForce RTX 3090; driver 535.113.01', or what could not be read.

    Reads the kernel module's /proc entries (the NVIDIA container runtime exposes
    them for the visible GPUs). Never raises: it only feeds a log line.
    """
    try:
        version = (proc / "version").read_text()
    except OSError:
        return f"no NVIDIA driver info ({proc / 'version'} unreadable)"
    match = _DRIVER_RE.search(version)
    driver = match.group(1) if match else "unknown"

    models = []
    for info in sorted((proc / "gpus").glob("*/information")):
        try:
            text = info.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("Model:"):
                models.append(line.split(":", 1)[1].strip())
                break
    return f"{', '.join(models) or 'no GPUs listed'}; driver {driver}"
