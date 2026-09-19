"""Pick the right compute device and precision for this machine."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)


def has_cuda() -> bool:
    """True if an NVIDIA GPU with a usable driver is present.

    Deliberately does not import torch - the app ships CTranslate2 and ONNX
    Runtime, not torch, so we probe the driver directly.
    """
    if os.environ.get("MUESLI_FORCE_CPU"):
        return False
    try:
        import ctypes
        for lib in ("nvcuda.dll", "libcuda.so.1"):
            try:
                ctypes.CDLL(lib)
                return True
            except OSError:
                continue
    except Exception:
        pass
    if shutil.which("nvidia-smi"):
        try:
            subprocess.run(["nvidia-smi"], capture_output=True, timeout=5, check=True)
            return True
        except Exception:
            return False
    return False


def resolve(device_pref: str = "auto", compute_pref: str = "auto") -> tuple[str, str]:
    """-> (device, compute_type) for CTranslate2/faster-whisper."""
    device = device_pref
    if device == "auto":
        device = "cuda" if has_cuda() else "cpu"
    elif device == "cuda" and not has_cuda():
        log.warning("cuda requested but no NVIDIA driver found; using cpu")
        device = "cpu"

    compute = compute_pref
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    log.info("compute: device=%s type=%s", device, compute)
    return device, compute
