# Notice

## Relationship to Muesli (macOS)

This project is an independent Windows implementation of the feature set of
[Muesli](https://github.com/pHequals7/muesli), a local-first dictation and
meeting-transcription app for macOS, which is MIT licensed.

**It contains no code from that project.** The macOS app is written in Swift
against Apple frameworks that have no Windows counterpart, so every part was
written from scratch:

| Muesli (macOS) | This project (Windows) |
|---|---|
| WhisperKit, FluidAudio (CoreML) | faster-whisper (CTranslate2), Parakeet (ONNX Runtime) |
| CoreAudio process tap, ScreenCaptureKit | WASAPI loopback |
| Accessibility API paste | SendInput (Unicode) with clipboard fallback |
| Carbon/AppKit hotkeys | `WH_KEYBOARD_LL` low-level keyboard hook |
| EventKit, CloudKit, Apple Shortcuts | not implemented |

This project is **not affiliated with, endorsed by, or supported by** the
authors of Muesli. Please do not raise issues about this project with them.

## Third-party components

Installed at runtime or bundled by the installer, each under its own licence:

| Component | Licence |
|---|---|
| PySide6 (Qt for Python) | LGPL v3 |
| faster-whisper, CTranslate2 | MIT |
| ONNX Runtime | MIT |
| onnx-asr | MIT |
| Whisper models (Systran, deepdml conversions) | MIT |
| Parakeet TDT models (NVIDIA NeMo, ONNX conversions) | CC-BY-4.0 |
| sounddevice / PortAudio | MIT |
| PyAudioWPatch | MIT |
| pycaw | MIT |
| reportlab | BSD |
| soxr | LGPL v2.1 |

Speech models are downloaded on first use rather than bundled; their licences
are those of the upstream model repositories.
