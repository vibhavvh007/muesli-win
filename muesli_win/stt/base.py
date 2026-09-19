"""Transcriber interface shared by every backend."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    text: str
    language: str = ""
    segments: list[Segment] = field(default_factory=list)
    backend: str = ""
    model: str = ""
    latency_ms: int = 0

    def __bool__(self) -> bool:
        return bool(self.text.strip())


class Transcriber(abc.ABC):
    name: str = "base"
    supports_streaming: bool = False

    @abc.abstractmethod
    def load(self) -> None:
        """Bring the model into memory. May be slow; call off the UI thread."""

    @abc.abstractmethod
    def transcribe(self, audio: np.ndarray, *, language: str = "auto",
                   prompt: str = "") -> Transcript:
        """`audio` is mono float32 at 16 kHz in [-1, 1]."""

    @property
    def loaded(self) -> bool:
        return getattr(self, "_loaded", False)

    def unload(self) -> None:
        self._loaded = False

    def warmup(self) -> None:
        """First inference is always slowest; burn it on silence at startup."""
        try:
            self.transcribe(np.zeros(16_000, dtype=np.float32))
        except Exception:
            pass


class TranscriptionError(RuntimeError):
    pass
