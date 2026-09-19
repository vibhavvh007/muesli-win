# Architecture

```
            ┌──────────────┐
  keyboard →│ winput.hook  │ WH_KEYBOARD_LL on its own thread, message loop
            └──────┬───────┘ callback only mutates a state machine, never blocks
                   │ Action
            ┌──────▼──────────────┐
            │ controller          │  orchestration, no UI, no Qt
            └──┬────────┬─────────┘
               │        │
      ┌────────▼──┐  ┌──▼──────────┐
      │ audio     │  │ stt         │ whisper (CTranslate2) | parakeet (ONNX) | cloud
      │ capture   │  │ registry    │ lazy load, background, graceful fallback chain
      └───────────┘  └──┬──────────┘
                        │ Transcript
                  ┌─────▼──────────┐
                  │ text           │ dictionary → voice commands → fillers → tidy
                  │ postprocess    │ optional LLM pass, output sanitised
                  └─────┬──────────┘
                  ┌─────▼──────────┐   ┌──────────┐
                  │ winput.injector│   │ storage  │ SQLite WAL
                  └────────────────┘   └──────────┘
                        │ Events (callbacks)
                  ┌─────▼──────────┐
                  │ app.Bridge     │ Qt signals — the only thread hop into the UI
                  └─────┬──────────┘
              tray · indicator · settings · dashboard
```

## Threading

| Thread | Does | Must never |
|---|---|---|
| hook | Win32 message loop, state machine | block — Windows unhooks a slow callback |
| hotkey-dispatch | runs controller actions | touch Qt |
| audio device | PortAudio/WASAPI callback, resample, enqueue | raise |
| dictation-pump | drains audio, watches idle/max timers | transcribe |
| dictation-transcribe | model inference, cleanup, injection, history | touch Qt |
| meeting-pump / meeting-stt | chunking and per-chunk inference | touch Qt |
| Qt main | every widget | do inference |

Everything crossing into the UI goes through `app.Bridge` signals with
`Qt.QueuedConnection`. That is the single rule that keeps the tray app from
crashing after twenty minutes.

## Why the state machine is separate

`winput/statemachine.py` has no Win32 in it and takes time as an argument. That
makes hold, double-tap, auto-repeat, hold-then-tap and cancel testable on any OS
in milliseconds — see `tests/test_statemachine.py`. The Win32 layer around it
only translates events and decides suppression.

## Failure posture

Every optional dependency degrades instead of crashing:

| Missing | Result |
|---|---|
| `pyaudiowpatch` | Meetings record your side only, and say so |
| `pycaw` | System audio is not muted while dictating |
| `soxr` | Falls back to a numpy filter + interpolation |
| `onnx-asr` | Parakeet unavailable; registry falls back to Whisper |
| `reportlab` | PDF export reports what to install; Markdown still works |
| Ollama not running | Post-processing is skipped, deterministic text still typed |
| CUDA present but broken | Model reloads on CPU `int8` |

The principle: a missing optional piece never costs the user their words.

## Config compatibility

`config.py` keeps macOS Muesli's key names so one file serves both machines.
Windows-only keys are prefixed `win_` and are ignored by the Mac app. Mac
hotkey keycodes and CoreML model ids are translated on load
(`map_backend`, `_mac_model_to_windows`), and credentials already on the PC are
protected from being blanked by an import.
