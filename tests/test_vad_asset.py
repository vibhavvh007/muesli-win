"""Regression: the frozen build shipped no Silero VAD asset.

Reported from a real Windows install (v1.0.1):

    transcription failed: [ONNXRuntimeError] : 3 : NO_SUCHFILE :
    Load model from ...\\Muesli\\_internal\\faster_whisper\\assets\\silero_vad_v6.onnx failed

Cause: faster-whisper ships that model as package DATA. The PyInstaller spec
collected submodules (code) and dynamic libraries, but `datas` was empty, so the
assets directory was never bundled. Everything else worked - hotkey, microphone,
model load - and it died on the first transcription.
"""
import pathlib

from muesli_win.stt.whisper_fw import WhisperTranscriber, _is_missing_asset

REAL_ERROR = (
    "[ONNXRuntimeError] : 3 : NO_SUCHFILE : Load model from "
    r"C:\Users\GauravDhudhani\AppData\Local\Programs\Muesli\_internal"
    r"\faster_whisper\assets\silero_vad_v6.onnx failed"
)


def test_the_reported_error_is_recognised_as_a_missing_asset():
    assert _is_missing_asset(Exception(REAL_ERROR)) is True


def test_unrelated_failures_are_not_treated_as_missing_assets():
    for msg in ("CUDA out of memory", "invalid audio input", "connection reset"):
        assert _is_missing_asset(Exception(msg)) is False


def test_vad_is_disabled_when_the_asset_is_absent(monkeypatch):
    """Rather than failing the dictation, drop VAD and still return text."""
    t = WhisperTranscriber(model="tiny", vad_filter=True)
    monkeypatch.setattr(WhisperTranscriber, "_vad_asset_available",
                        staticmethod(lambda: False))

    class FakeModel:
        def transcribe(self, *a, **kw):
            assert kw["vad_filter"] is False      # must not ask for the missing model
            return [], type("I", (), {"language": "en"})()

    def fake_load(self):
        self._model = FakeModel()
        if self.vad_filter and not self._vad_asset_available():
            self.vad_filter = False
        self._loaded = True

    monkeypatch.setattr(WhisperTranscriber, "load", fake_load)
    t.load()
    assert t.vad_filter is False
    import numpy as np
    assert t.transcribe(np.zeros(16_000, dtype=np.float32)).text == ""


def test_runtime_vad_failure_retries_without_vad(monkeypatch):
    t = WhisperTranscriber(model="tiny", vad_filter=True)
    calls = []

    class FlakyModel:
        def transcribe(self, *a, **kw):
            calls.append(kw["vad_filter"])
            if kw["vad_filter"]:
                raise RuntimeError(REAL_ERROR)
            return [], type("I", (), {"language": "en"})()

    t._model = FlakyModel()
    t._loaded = True
    import numpy as np
    result = t.transcribe(np.zeros(16_000, dtype=np.float32))
    assert calls == [True, False]                 # tried VAD, then fell back
    assert result.text == ""
    assert t.vad_filter is False                  # and stops retrying next time


def test_spec_collects_data_files_not_only_submodules():
    """The packaging fix itself, asserted so it cannot silently regress."""
    spec = (pathlib.Path(__file__).resolve().parent.parent
            / "packaging" / "muesli.spec").read_text()
    assert "collect_data_files" in spec, "spec must collect package data, not just code"
    assert "faster_whisper" in spec, "faster_whisper must be in the data-file list"
    assert "datas=datas" in spec, "both Analysis blocks must receive the collected data"
    assert "datas=[]" not in spec, "an empty datas list is what caused the bug"
