"""Parakeet TDT via ONNX Runtime - the low-latency option.

Muesli's headline dictation number (~0.13 s) comes from Parakeet TDT on the
Apple Neural Engine. The same weights run on Windows through ONNX Runtime, on
DirectML/CUDA where available and CPU otherwise. Decoding an RNN-T/TDT model by
hand is fiddly, so this leans on `onnx-asr`, which ships that decoder.
"""
from __future__ import annotations

import logging
import time

import numpy as np

from .. import paths
from .base import Transcriber, Transcript, TranscriptionError
from .compute import has_cuda

log = logging.getLogger(__name__)

MODELS = {
    "parakeet-tdt-0.6b-v3": "istupakov/parakeet-tdt-0.6b-v3-onnx",
    "parakeet-tdt-0.6b-v2": "istupakov/parakeet-tdt-0.6b-v2-onnx",
    "parakeet-ctc-0.6b": "istupakov/parakeet-ctc-0.6b-onnx",
}
# The repos carry both an fp32 encoder (a 2.3 GB external-weights blob) and an
# int8 one. int8 is the default here: 640 MB instead of 2.4 GB, and faster on the
# CPU that most Windows laptops will be using. Sizes measured, not estimated.
APPROX_SIZE_MB = {
    "parakeet-tdt-0.6b-v3": 640,
    "parakeet-tdt-0.6b-v2": 640,
    "parakeet-ctc-0.6b": 620,
}
APPROX_SIZE_MB_FP32 = {
    "parakeet-tdt-0.6b-v3": 2370,
    "parakeet-tdt-0.6b-v2": 2370,
    "parakeet-ctc-0.6b": 2340,
}
DEFAULT_QUANTIZATION = "int8"


def size_mb(model: str, quantization: str | None = DEFAULT_QUANTIZATION) -> int:
    table = APPROX_SIZE_MB if quantization == "int8" else APPROX_SIZE_MB_FP32
    return table.get(model, 0)


def _providers() -> list[str]:
    try:
        import onnxruntime as ort
        available = set(ort.get_available_providers())
    except ImportError:
        return ["CPUExecutionProvider"]
    order = []
    if has_cuda() and "CUDAExecutionProvider" in available:
        order.append("CUDAExecutionProvider")
    if "DmlExecutionProvider" in available:       # DirectML: any Win GPU, incl. AMD/Intel
        order.append("DmlExecutionProvider")
    order.append("CPUExecutionProvider")
    return order


class ParakeetTranscriber(Transcriber):
    name = "parakeet"

    def __init__(self, model: str = "parakeet-tdt-0.6b-v3", language: str = "auto",
                 quantization: str | None = DEFAULT_QUANTIZATION) -> None:
        self.model_name = model if model in MODELS else "parakeet-tdt-0.6b-v3"
        self.language = language
        self.quantization = quantization or None
        self._model = None
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        try:
            import onnx_asr
        except ImportError as exc:
            raise TranscriptionError(
                "onnx-asr is not installed - run: pip install onnx-asr onnxruntime"
            ) from exc
        t0 = time.monotonic()
        try:
            self._model = onnx_asr.load_model(
                MODELS[self.model_name],
                path=str(paths.models_dir() / "parakeet"),
                quantization=self.quantization,
                providers=_providers(),
            )
        except Exception as exc:
            if self.quantization:
                # An int8 variant may be missing from a repo; fp32 always exists.
                log.warning("int8 load failed (%s); retrying at full precision", exc)
                try:
                    self._model = onnx_asr.load_model(
                        MODELS[self.model_name],
                        path=str(paths.models_dir() / "parakeet"),
                        providers=_providers(),
                    )
                    self.quantization = None
                except Exception as exc2:
                    raise TranscriptionError(
                        f"could not load {self.model_name}: {exc2}") from exc2
            else:
                raise TranscriptionError(
                    f"could not load {self.model_name}: {exc}") from exc
        self._loaded = True
        log.info("parakeet %s (%s) loaded in %.1fs via %s", self.model_name,
                 self.quantization or "fp32", time.monotonic() - t0, _providers()[0])

    def transcribe(self, audio: np.ndarray, *, language: str = "auto",
                   prompt: str = "") -> Transcript:
        if not self._loaded:
            self.load()
        if audio.size == 0:
            return Transcript(text="", backend=self.name, model=self.model_name)
        t0 = time.monotonic()
        try:
            text = self._model.recognize(audio.astype(np.float32), sample_rate=16_000)
        except Exception as exc:
            raise TranscriptionError(f"transcription failed: {exc}") from exc
        if isinstance(text, (list, tuple)):
            text = " ".join(str(t) for t in text)
        return Transcript(text=str(text).strip(), backend=self.name,
                          model=self.model_name,
                          latency_ms=int((time.monotonic() - t0) * 1000))

    def unload(self) -> None:
        self._model = None
        self._loaded = False
