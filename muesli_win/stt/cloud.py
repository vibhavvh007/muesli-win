"""Hosted transcription (OpenAI-compatible /v1/audio/transcriptions).

Optional, off by default, and it is the only path that sends audio off the
machine - so it is labelled as such everywhere in the UI.
"""
from __future__ import annotations

import io
import logging
import time
import wave

import numpy as np

from .base import Transcriber, Transcript, TranscriptionError

log = logging.getLogger(__name__)

ENDPOINTS = {
    "openai": "https://api.openai.com/v1/audio/transcriptions",
    "openrouter": "https://openrouter.ai/api/v1/audio/transcriptions",
    "groq": "https://api.groq.com/openai/v1/audio/transcriptions",
}


def to_wav_bytes(audio: np.ndarray, rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class CloudTranscriber(Transcriber):
    name = "cloud"

    def __init__(self, provider: str = "openai", api_key: str = "",
                 model: str = "whisper-1", base_url: str = "",
                 language: str = "auto", timeout: int = 120) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model_name = model or "whisper-1"
        self.base_url = base_url or ENDPOINTS.get(provider, ENDPOINTS["openai"])
        self.language = language
        self.timeout = timeout
        self._loaded = False

    def load(self) -> None:
        if not self.api_key:
            raise TranscriptionError(f"no API key configured for {self.provider}")
        self._loaded = True

    def transcribe(self, audio: np.ndarray, *, language: str = "auto",
                   prompt: str = "") -> Transcript:
        if not self._loaded:
            self.load()
        if audio.size == 0:
            return Transcript(text="", backend=self.name, model=self.model_name)
        try:
            import requests
        except ImportError as exc:
            raise TranscriptionError("requests is not installed") from exc

        lang = language if language != "auto" else self.language
        data = {"model": self.model_name}
        if lang not in ("auto", "", None):
            data["language"] = lang
        if prompt:
            data["prompt"] = prompt

        t0 = time.monotonic()
        try:
            resp = requests.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("audio.wav", to_wav_bytes(audio), "audio/wav")},
                data=data, timeout=self.timeout,
            )
        except Exception as exc:
            raise TranscriptionError(f"network error: {exc}") from exc
        if resp.status_code != 200:
            raise TranscriptionError(
                f"{self.provider} returned {resp.status_code}: {resp.text[:200]}"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise TranscriptionError("provider returned invalid JSON") from exc
        return Transcript(
            text=str(payload.get("text", "")).strip(),
            language=str(payload.get("language", "") or ""),
            backend=f"{self.name}:{self.provider}", model=self.model_name,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
