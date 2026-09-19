"""Builds the right transcriber from config, and degrades gracefully.

Dictation must never be dead in the water because one backend is missing, so a
failed build falls back down a chain: requested -> whisper -> parakeet, and the
UI is told which one actually answered.
"""
from __future__ import annotations

import logging
import threading

from . import cloud, parakeet, whisper_fw
from .base import Transcriber, TranscriptionError

log = logging.getLogger(__name__)

BACKENDS = ("whisper", "parakeet", "openai", "openrouter", "groq")


def catalog() -> dict[str, dict]:
    """Everything selectable in Settings, with download sizes."""
    out: dict[str, dict] = {}
    for name in whisper_fw.MODELS:
        out[name] = {"backend": "whisper", "size_mb": whisper_fw.APPROX_SIZE_MB.get(name, 0),
                     "local": True}
    for name in parakeet.MODELS:
        out[name] = {"backend": "parakeet", "size_mb": parakeet.APPROX_SIZE_MB.get(name, 0),
                     "local": True}
    out["whisper-1"] = {"backend": "openai", "size_mb": 0, "local": False}
    out["gpt-4o-transcribe"] = {"backend": "openai", "size_mb": 0, "local": False}
    return out


def build(cfg, *, purpose: str = "dictation") -> Transcriber:
    if purpose == "meeting":
        backend = cfg.get("meeting_transcription_backend", "whisper")
        model = cfg.get("meeting_transcription_model", "large-v3")
    else:
        backend = cfg.get("stt_backend", "whisper")
        model = cfg.get("stt_model", "large-v3")
    language = cfg.get("whisper_language", "auto")

    def make(b: str, m: str) -> Transcriber:
        if b == "whisper":
            return whisper_fw.WhisperTranscriber(
                model=m, device=cfg.get("win_compute_device", "auto"),
                compute_type=cfg.get("win_compute_type", "auto"), language=language)
        if b == "parakeet":
            pm = m if m in parakeet.MODELS else cfg.get(
                "whisper_model", "parakeet-tdt-0.6b-v3")
            return parakeet.ParakeetTranscriber(model=pm, language=language)
        if b in ("openai", "openrouter", "groq"):
            return cloud.CloudTranscriber(
                provider=b, api_key=cfg.get(f"{b}_api_key", ""),
                model=m or "whisper-1", language=language)
        raise TranscriptionError(f"unknown backend {b!r}")

    chain = [(backend, model)]
    if backend != "whisper":
        chain.append(("whisper", "small"))
    if backend != "parakeet":
        chain.append(("parakeet", "parakeet-tdt-0.6b-v3"))

    last: Exception | None = None
    for b, m in chain:
        try:
            t = make(b, m)
            if b != backend:
                log.warning("falling back to %s/%s", b, m)
            return t
        except Exception as exc:
            last = exc
            log.error("backend %s unavailable: %s", b, exc)
    raise TranscriptionError(f"no transcription backend available ({last})")


class LazyTranscriber:
    """Holds the model, loads it once in the background, swaps it on config change.

    Dictation blocks on `wait_ready()` rather than starting a second load, so a
    hotkey pressed during startup just waits instead of thrashing the GPU.
    """

    def __init__(self, cfg, purpose: str = "dictation") -> None:
        self.cfg = cfg
        self.purpose = purpose
        self._t: Transcriber | None = None
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._error: str = ""
        self._loading = False

    @property
    def error(self) -> str:
        return self._error

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def describe(self) -> str:
        t = self._t
        return f"{t.name}/{getattr(t, 'model_name', '?')}" if t else "(none)"

    def preload(self) -> None:
        with self._lock:
            if self._loading or self._ready.is_set():
                return
            self._loading = True
        threading.Thread(target=self._load, name="stt-preload", daemon=True).start()

    def _load(self) -> None:
        try:
            t = build(self.cfg, purpose=self.purpose)
            t.load()
            with self._lock:
                self._t = t
                self._error = ""
            self._ready.set()
            t.warmup()
            log.info("%s transcriber ready: %s", self.purpose, self.describe())
        except Exception as exc:
            self._error = str(exc)
            log.error("%s transcriber failed to load: %s", self.purpose, exc)
        finally:
            with self._lock:
                self._loading = False

    def wait_ready(self, timeout: float = 90.0) -> bool:
        if self._ready.is_set():
            return True
        self.preload()
        return self._ready.wait(timeout)

    def reload(self) -> None:
        with self._lock:
            old, self._t = self._t, None
            self._ready.clear()
            self._error = ""
        if old:
            try:
                old.unload()
            except Exception:
                pass
        self.preload()

    def transcribe(self, audio, **kw):
        if not self.wait_ready():
            raise TranscriptionError(self._error or "transcriber not ready")
        with self._lock:
            t = self._t
        if t is None:
            raise TranscriptionError(self._error or "transcriber unavailable")
        return t.transcribe(audio, **kw)
