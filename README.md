# Muesli for Windows

Local-first dictation and meeting transcription for Windows — a ground-up
Windows build of the feature set of [Muesli](https://github.com/pHequals7/muesli)
(macOS, MIT). Speech-to-text runs on this PC; nothing is uploaded unless you
deliberately choose a hosted engine.

Ships as `Muesli-Setup-1.0.2.exe` — a per-user installer, no admin rights needed.

```
Hold  Right Alt          talk, release, the text types itself
Tap   Right Alt twice    hands-free — talk as long as you like, tap once to stop
Esc   while recording    discard the take
```

---

## What it does

**Dictation**
- Hold-to-talk and double-tap hands-free, on a configurable key
- Types into whatever window has focus — Word, Outlook, Slack, a browser, a terminal
- Whisper (`large-v3` … `tiny`) or Parakeet TDT, on NVIDIA GPU when present, CPU otherwise
- Personal dictionary with fuzzy matching, so *"ultra human"* → **Ultrahuman**
- Filler-word removal, `scratch that`, and spoken punctuation (`full stop`, `new paragraph`)
- Optional LLM cleanup via local Ollama / LM Studio, or OpenAI / OpenRouter
- **Quill** — select text, press Right Ctrl, say *"make this more formal"*, it rewrites in place
- Mutes other apps while you dictate without pausing them, so a video keeps its place

**Meetings**
- Captures your microphone **and** the system audio, labelled *You* and *Others*
- Chunks on natural pauses, so nothing is cut mid-word
- AI notes from five templates (general, stand-up, 1:1, interview, client call), auto-picked
- Export to Markdown or paginated PDF; optional post-meeting hook program
- Audio is discarded by default — you keep the transcript, not the recording

**Everything else**
- Tray icon, draggable floating indicator, dashboard with full history
- SQLite (WAL) on this machine only. No cloud, no account, no telemetry
- `muesli-cli` — JSON-first command line for scripts and agents

---

## Install

Download `Muesli-Setup-1.0.2.exe` from the
[Releases](../../releases) page and run it. Windows 10 1809 or later, 64-bit.

On first launch open **Settings → Models**, pick a model and press **Download**.
The table shows each model's size and whether it is already on disk, and the
progress bar tells you how far along it is - so the first hotkey press is
instant rather than a mystery pause.

| Model | Size | Use it when |
|---|---|---|
| `parakeet-tdt-0.6b-v3` | 640 MB | You want the fastest dictation. English-first. Measured at 194 ms for 5.9 s of speech on a CPU |
| `large-v3-turbo` | 1.6 GB | Good accuracy, multilingual, no GPU |
| `large-v3` | 3 GB | Most accurate, multilingual. Comfortable with an NVIDIA GPU |
| `small` / `base` | 480 / 145 MB | Older laptop, responsiveness over accuracy |

Or pre-download from the command line:

```powershell
muesli-cli models list
muesli-cli models download parakeet-tdt-0.6b-v3 --use
```

Models land in `%LOCALAPPDATA%\Muesli\cache\models`.

### Coming from a Mac

Copy `~/Library/Application Support/Muesli/config.json` across and use
**Settings → Advanced → Import settings from a Mac config.json**. Hotkeys,
dictionary, prompts and preferences all carry over:

| On the Mac | Becomes |
|---|---|
| `Right Option` | `Right Alt` |
| `Right Cmd` | `Right Win` |
| WhisperKit `large-v3-…_626MB` | Whisper `large-v3` |
| `FluidInference/parakeet-…-coreml` | `parakeet-tdt-0.6b-v3` |
| Apple Speech, Cohere, SenseVoice, Nemotron, Bodhan, Gemma | Whisper `large-v3` |

API keys already stored on the PC are never overwritten by an import.

---

## Requirements and what affects speed

| | |
|---|---|
| **OS** | Windows 10 1809+ / Windows 11, x64 |
| **GPU** | Optional. An NVIDIA GPU is detected automatically and used via CUDA |
| **CPU only** | Works. Use `large-v3-turbo`, `small`, or Parakeet for latency |
| **Disk** | ~350 MB for the app, plus whichever models you download |
| **System audio** | Needs `pyaudiowpatch` (bundled in the installer) |

---

## Feature parity with macOS Muesli

Honest accounting. Most of the gap is Apple frameworks with no Windows counterpart.

### Built
Hold-to-talk · double-tap hands-free · configurable hotkey · text injection ·
Whisper (all sizes) · Parakeet TDT · hosted OpenAI/OpenRouter ·
mic + system-audio meeting capture · VAD chunking · meeting notes with templates ·
Markdown/PDF export · post-meeting hooks · personal dictionary with fuzzy matching ·
filler removal · `scratch that` · LLM post-processing · tray · floating indicator ·
dashboard · SQLite history · CLI · launch at login · audio-file transcription ·
Quill voice rewriting

### Not built, deliberately

| Muesli feature | Why not |
|---|---|
| Apple Speech, Cohere, SenseVoice, Nemotron, Bodhan, Gemma engines | CoreML-only builds. Mapped to Whisper `large-v3`, which is multilingual and covers the same languages |
| iCloud sync, iPhone Bridge | CloudKit. No Windows equivalent; history stays local |
| Apple Shortcuts / Siri | macOS only. `muesli-cli` covers the scripting case |
| macOS Calendar integration, Join & Transcribe | EventKit. Would need Microsoft Graph — a separate piece of work |
| Speaker diarization | Needs a pyannote ONNX pipeline. Streams are labelled *You* / *Others*, but individual remote speakers are not separated |
| Camera-based meeting detection | Config carries a process-name detector (`win_meeting_detection_*`); the camera heuristic is not implemented |
| Echo cancellation (LocalVQE / DTLN) | Not ported. Use headphones for clean meeting capture |
| Computer-use planner | Out of scope for a dictation tool |

---

## Known Windows limitations, stated plainly

- **Elevated windows.** Windows (UIPI) forbids a normal process sending keystrokes
  to a window running as administrator. If you dictate into an elevated app,
  nothing is typed. Run Muesli elevated too, or use the dashboard to copy the text.
- **Right Alt is AltGr** on German, Polish, most non-US European and US-International
  layouts. Muesli holds the hotkey exclusively, so on those layouts `@ \ € …` would
  stop working. It detects this and tells you to pick **Right Ctrl** or **F13**.
- **The keyboard hook can be dropped.** If a hook callback ever exceeds Windows'
  `LowLevelHooksTimeout`, Windows silently unhooks it. The callback here does
  nothing but update a state machine, but if the hotkey ever goes quiet,
  **tray → Re-arm hotkey** reinstalls it.
- **Clipboard paste** is used for text over ~220 characters because synthetic
  keystrokes get slow and lossy at length. The previous clipboard contents are
  restored afterwards.

---

## `muesli-cli`

```powershell
muesli-cli info                                  # paths, model, compute device
muesli-cli devices                               # microphones and loopback endpoints
muesli-cli transcribe recording.wav --format srt
muesli-cli dictations get last
muesli-cli meetings list --limit 5
muesli-cli meetings summarise last --template client_call
muesli-cli meetings export last --format pdf --content both
muesli-cli config set stt_model '"large-v3-turbo"'
```

Everything returns JSON on stdout and a non-zero exit code on failure.

---

## Build it yourself

```powershell
git clone <this repo>; cd muesli-win
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Produces `dist\Muesli\Muesli.exe` and `dist\Muesli-Setup-1.0.2.exe`.
See [docs/BUILD.md](docs/BUILD.md) for the detail, and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how it fits together.

Every push builds and smoke-tests the `.exe` on `windows-latest`; the installer
is attached to the run as an artifact.

## Licence

MIT - see [LICENSE](LICENSE).

This is an independent Windows implementation of Muesli's feature set, not a
port: it shares no code with the macOS app and is not affiliated with or
supported by its authors. See [NOTICE.md](NOTICE.md) for the full mapping and
for third-party component licences.
