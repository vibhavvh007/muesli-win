# Rolling Muesli out to a team

Written for the person handing this to colleagues, not for developers.

## Before you distribute

### 1. Decide where transcripts go

Dictation and meeting transcription run **on the machine**. Nothing is uploaded
— with two exceptions you control:

| Setting | Sends data out? |
|---|---|
| `stt_backend` = `whisper` or `parakeet` | No. Audio never leaves the PC |
| `stt_backend` = `openai` / `openrouter` / `groq` | **Yes — audio is uploaded** |
| `meeting_summary_backend` = `ollama` / `lmstudio` | No. Runs locally |
| `meeting_summary_backend` = `openrouter` / `openai` | **Yes — the transcript is uploaded** |
| `post_processor_backend` = `local` / `ollama` | No |

The shipped default for meeting notes is `openrouter`. It does nothing without
an API key, so out of the box the app is fully local — but anyone who pastes a
key in is sending meeting transcripts to a third party. If colleagues will be
transcribing supplier, customer, auditor or candidate calls, decide this
deliberately rather than by default.

**To keep a team fully local**, ship the config in the next section and either
run Ollama locally or leave notes generation off.

### 2. Recording consent

The app can transcribe the other side of a call. Whether participants must be
told is a legal and policy question, not a technical one, and it varies by
jurisdiction and by who is on the call. Settle it before a team rollout, not
after. Audio itself is discarded by default (`meeting_recording_save_policy:
"never"`) — only the transcript is kept, on the user's own machine.

### 3. Unsigned installer

The installer is not code-signed, so every user sees **"Windows protected your
PC"** and must click *More info → Run anyway*. That is unavoidable without a
certificate, and teaching a team to click through SmartScreen warnings is its
own small risk. Options:

- Accept it, and tell people in advance so the warning is expected
- Sign it (Azure Trusted Signing is the cheapest current route), then the
  warning disappears
- On managed devices, ask IT to allow-list the publisher

On a company-managed Windows device, an unsigned per-user installer may be
blocked outright by policy. Check one machine before promising it to ten people.

---

## A locked-down team config

Save as `config.json` in `%APPDATA%\Muesli\` after first launch, or import it
via **Settings → Advanced**. This keeps everything on the machine.

```json
{
  "stt_backend": "whisper",
  "stt_model": "large-v3-turbo",
  "whisper_language": "auto",
  "dictation_hotkey": { "vk": 163, "label": "Right Ctrl" },
  "enable_double_tap_dictation": true,
  "meeting_recording_save_policy": "never",
  "meeting_summary_backend": "ollama",
  "post_processor_backend": "local",
  "enable_post_processor": false,
  "icloud_sync_enabled": false,
  "auto_record_meetings": false,
  "custom_words": [
    { "word": "ultrahuman", "replacement": "Ultrahuman", "matching_threshold": 0.85 }
  ]
}
```

`Right Ctrl` rather than `Right Alt` is deliberate: it avoids the AltGr clash on
any colleague using a non-US keyboard layout.

Add your own vocabulary to `custom_words` before distributing — product names,
counterparties, colleagues. It is the single biggest accuracy win and it costs
nothing.

---

## What to send a colleague

> **Muesli — dictation for Windows**
>
> 1. Download the installer, extract the `.zip`, run `Muesli-Setup-1.0.0.exe`.
> 2. Windows will warn that the publisher is unknown. Click **More info → Run anyway**.
> 3. On first launch, open **Settings → Models** and pick `large-v3-turbo`.
>    It downloads about 1.6 GB once — do it on a decent connection.
> 4. Open Notepad, **hold Right Ctrl**, say a sentence, let go. It types itself.
>    Double-tap Right Ctrl for hands-free; tap once to stop. Esc discards.
>
> Everything runs on your own laptop. Nothing is uploaded and no account is needed.
> If the key does nothing, right-click the tray icon → **Re-arm hotkey**.

---

## Machine requirements

| | |
|---|---|
| Windows | 10 1809+ or 11, 64-bit |
| Disk | ~350 MB app + 1.6–3 GB for the model |
| RAM | 8 GB is comfortable with `large-v3-turbo` on CPU |
| GPU | Optional. NVIDIA is detected and used automatically |

On an older laptop with no GPU, use `small` or `base` instead — less accurate,
but responsive. `large-v3` on a CPU-only machine is slow enough to be annoying.

---

## Supporting it

Ask for these two things; they identify almost any problem:

```
%APPDATA%\Muesli\logs\muesli.log
"C:\Program Files\Muesli\muesli-cli.exe" info
```

| Symptom | Cause |
|---|---|
| Hotkey does nothing | Hook dropped — tray → **Re-arm hotkey**. If it recurs, another app is fighting for the hook |
| Records but types nothing | The target window is running as administrator (Windows blocks it), or use clipboard mode in Settings → Advanced |
| First dictation hangs | Model still downloading — check Settings → Models |
| AltGr characters stopped working | Hotkey is on Right Alt on an AltGr layout — switch to Right Ctrl |
| Meeting only captured your voice | Loopback device unavailable; check Settings → Meetings → System audio |
