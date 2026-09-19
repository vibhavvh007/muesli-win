"""Fetch a speech model ahead of time.

Without this the first model download happens on the first hotkey press, which
looks exactly like the app hanging. Downloading through the real loader rather
than a raw snapshot fetch is deliberate: it guarantees the on-disk cache layout
is the one the backend will look for, and it proves the model actually loads.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from .. import paths
from . import parakeet, whisper_fw
from .base import TranscriptionError

log = logging.getLogger(__name__)


def catalog() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, repo in whisper_fw.MODELS.items():
        out[name] = {"backend": "whisper", "repo": repo,
                     "size_mb": whisper_fw.APPROX_SIZE_MB.get(name, 0),
                     "dir": paths.models_dir() / "whisper"}
    for name, repo in parakeet.MODELS.items():
        out[name] = {"backend": "parakeet", "repo": repo,
                     "size_mb": parakeet.size_mb(name),
                     "dir": paths.models_dir() / "parakeet"}
    return out


def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _looks_installed(meta: dict) -> bool:
    """A model is present if its cache folder holds most of the expected bytes."""
    root: Path = meta["dir"]
    if not root.exists():
        return False
    slug = meta["repo"].replace("/", "--")
    for child in root.rglob("*"):
        if child.is_dir() and slug.lower() in child.name.lower():
            return _dir_bytes(child) > meta["size_mb"] * 1024 * 1024 * 0.9
    # some loaders lay the files out flat
    return _dir_bytes(root) > meta["size_mb"] * 1024 * 1024 * 0.9


def backend_dir(name: str) -> Path:
    meta = catalog().get(name)
    return meta["dir"] if meta else paths.models_dir()


def bytes_on_disk(name: str) -> int:
    """Total bytes in the backend cache folder for `name`.

    Progress is measured by watching this grow rather than by hooking the
    downloader: huggingface_hub gives no usable callback through the loaders we
    call, and the folder layout differs between backends. Watching the bytes is
    layout-agnostic and honest about what is actually on disk.
    """
    return _dir_bytes(backend_dir(name))


def expected_bytes(name: str) -> int:
    meta = catalog().get(name)
    return int((meta["size_mb"] if meta else 0) * 1024 * 1024)


def status() -> list[dict]:
    rows = []
    for name, meta in catalog().items():
        rows.append({
            "name": name, "backend": meta["backend"], "repo": meta["repo"],
            "size_mb": meta["size_mb"], "installed": _looks_installed(meta),
        })
    return rows


def download(name: str, *, device: str = "auto", compute: str = "auto") -> dict:
    """Fetch and load `name`. Raises TranscriptionError with a plain message."""
    cat = catalog()
    if name not in cat:
        raise TranscriptionError(
            f"unknown model {name!r}. Known: {', '.join(sorted(cat))}")
    meta = cat[name]
    free = shutil.disk_usage(paths.models_dir()).free / (1024 ** 2)
    if meta["size_mb"] and free < meta["size_mb"] * 1.3:
        raise TranscriptionError(
            f"not enough disk space: {name} needs about {meta['size_mb']} MB "
            f"and only {int(free)} MB is free")

    paths.models_dir().mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    if meta["backend"] == "whisper":
        t = whisper_fw.WhisperTranscriber(model=name, device=device, compute_type=compute)
    else:
        t = parakeet.ParakeetTranscriber(model=name)      # int8 by default
    t.load()                        # downloads on first use, then loads
    t.unload()
    return {
        "name": name, "backend": meta["backend"], "repo": meta["repo"],
        "seconds": round(time.monotonic() - t0, 1),
        "path": str(meta["dir"]),
    }
