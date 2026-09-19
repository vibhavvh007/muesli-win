"""Whisper via faster-whisper (CTranslate2).

This is the direct analogue of Muesli's WhisperKit backend: same model family,
same weights, running on CUDA when an NVIDIA GPU is present and on CPU int8
otherwise. Models are pulled from Hugging Face on first use into the app cache,
matching Muesli's download-on-demand behaviour rather than bloating the
installer.
"""
from __future__ import annotations

import logging
import time

import numpy as np

from .. import paths
from .base import Segment, Transcriber, Transcript, TranscriptionError
from .compute import resolve

log = logging.getLogger(__name__)

# Friendly name -> CTranslate2 repo. English-only variants included because they
# are meaningfully faster for en-only dictation.
MODELS = {
    "tiny": "Systran/faster-whisper-tiny",
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "base": "Systran/faster-whisper-base",
    "base.en": "Systran/faster-whisper-base.en",
    "small": "Systran/faster-whisper-small",
    "small.en": "Systran/faster-whisper-small.en",
    "medium": "Systran/faster-whisper-medium",
    "medium.en": "Systran/faster-whisper-medium.en",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}

APPROX_SIZE_MB = {
    "tiny": 75, "tiny.en": 75, "base": 145, "base.en": 145,
    "small": 480, "small.en": 480, "medium": 1530, "medium.en": 1530,
    "large-v3": 3090, "large-v3-turbo": 1620, "distil-large-v3": 1510,
}


class WhisperTranscriber(Transcriber):
    name = "whisper"

    def __init__(self, model: str = "large-v3", device: str = "auto",
                 compute_type: str = "auto", language: str = "auto",
                 beam_size: int = 5, vad_filter: bool = True) -> None:
        self.model_name = model if model in MODELS else "large-v3"
        if model not in MODELS:
            log.warning("unknown whisper model %r; using large-v3", model)
        self.device_pref, self.compute_pref = device, compute_type
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self._model = None
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError(
                "faster-whisper is not installed - run: pip install faster-whisper"
            ) from exc

        device, compute_type = resolve(self.device_pref, self.compute_pref)
        repo = MODELS[self.model_name]
        cache = str(paths.models_dir() / "whisper")
        t0 = time.monotonic()
        try:
            self._model = WhisperModel(repo, device=device, compute_type=compute_type,
                                       download_root=cache)
        except Exception as exc:
            if device == "cuda":
                log.warning("cuda load failed (%s); retrying on cpu", exc)
                self._model = WhisperModel(repo, device="cpu", compute_type="int8",
                                           download_root=cache)
            else:
                raise TranscriptionError(f"could not load {self.model_name}: {exc}") from exc
        self._loaded = True
        log.info("whisper %s loaded in %.1fs", self.model_name, time.monotonic() - t0)

    def transcribe(self, audio: np.ndarray, *, language: str = "auto",
                   prompt: str = "") -> Transcript:
        if not self._loaded:
            self.load()
        if audio.size == 0:
            return Transcript(text="", backend=self.name, model=self.model_name)

        lang = language if language != "auto" else self.language
        lang_arg = None if lang in ("auto", "", None) else lang
        t0 = time.monotonic()
        try:
            segments, info = self._model.transcribe(
                audio.astype(np.float32), language=lang_arg,
                beam_size=self.beam_size, vad_filter=self.vad_filter,
                initial_prompt=prompt or None,
                condition_on_previous_text=False,   # stops runaway repetition
            )
            segs = [Segment(s.start, s.end, s.text.strip()) for s in segments]
        except Exception as exc:
            raise TranscriptionError(f"transcription failed: {exc}") from exc

        text = " ".join(s.text for s in segs).strip()
        return Transcript(
            text=text, language=getattr(info, "language", "") or "",
            segments=segs, backend=self.name, model=self.model_name,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    def unload(self) -> None:
        self._model = None
        self._loaded = False
