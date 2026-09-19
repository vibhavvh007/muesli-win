"""Configuration.

Key names are deliberately identical to macOS Muesli's config.json so an
existing config merges in unchanged. Windows-only keys are prefixed `win_`
and are ignored by the Mac app, so one file can serve both machines.

Mac hotkeys are stored as macOS virtual keycodes (Right Option = 61). Those are
meaningless on Windows, so hotkeys carry an optional `vk` (Windows virtual-key)
alongside; when `vk` is absent we translate from the Mac `keyCode`/`label`.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Any

from . import paths

log = logging.getLogger(__name__)

# --- Windows virtual-key codes we care about -------------------------------
VK = {
    "Right Alt": 0xA5,      # VK_RMENU
    "Left Alt": 0xA4,       # VK_LMENU
    "Right Ctrl": 0xA3,     # VK_RCONTROL
    "Left Ctrl": 0xA2,      # VK_LCONTROL
    "Right Shift": 0xA1,    # VK_RSHIFT
    "Left Shift": 0xA0,     # VK_LSHIFT
    "Right Win": 0x5C,      # VK_RWIN
    "Left Win": 0x5B,       # VK_LWIN
    "Caps Lock": 0x14,
    "F13": 0x7C,
    "F14": 0x7D,
    "F15": 0x7E,
    "Scroll Lock": 0x91,
    "Pause": 0x13,
}
VK_NAME = {v: k for k, v in VK.items()}

# macOS keyCode -> the nearest sane Windows key.
# Right Option is the Mac default; Right Alt is its physical twin on a PC board.
MAC_KEYCODE_TO_VK = {
    61: VK["Right Alt"],      # Right Option
    58: VK["Left Alt"],       # Left Option
    54: VK["Right Win"],      # Right Cmd
    55: VK["Left Win"],       # Left Cmd
    62: VK["Right Ctrl"],     # Right Control
    59: VK["Left Ctrl"],      # Left Control
    60: VK["Right Shift"],    # Right Shift
    56: VK["Left Shift"],     # Left Shift
}

DEFAULTS: dict[str, Any] = {
    # ---- dictation ----
    "dictation_hotkey": {"keyCode": 61, "label": "Right Option", "vk": VK["Right Alt"]},
    "hotkey_trigger_threshold_ms": 250,
    "enable_double_tap_dictation": True,
    "double_tap_window_ms": 400,
    "stt_backend": "whisper",              # whisper | parakeet | openai | openrouter
    "stt_model": "large-v3",
    "whisper_language": "auto",
    "whisper_model": "parakeet-tdt-0.6b-v3",
    "enable_live_streaming_partials": False,
    "mute_system_audio_during_dictation": True,
    "pause_media_during_dictation": False,
    "sound_enabled": True,
    "idle_timeout": 120,                   # seconds of hands-free silence -> auto stop
    "max_dictation_seconds": 600,

    # ---- dictionary / post-processing ----
    "custom_words": [],
    "enable_post_processor": False,
    "post_processor_backend": "local",     # local | ollama | openai | openrouter | lmstudio | custom
    "active_post_processor_id": "qwen35-postproc-v3",
    "post_processor_system_prompt": (
        "Clean up speech-to-text transcription. Only make changes when there is a clear "
        "error. If the text is already correct, output it exactly as-is.\n\n"
        "You may: fix obvious misspellings, remove filler words (um, uh, like), apply "
        "'scratch that' deletions, and format numbered or bullet lists when dictated.\n\n"
        "Do not: paraphrase, reword, add words, remove meaningful words, change the meaning "
        "in any way, wrap the output in markdown, code fences, tags, labels, or commentary, "
        "or repeat the output more than once. Preserve the speaker's original phrasing."
    ),
    "strip_filler_words": True,
    "enable_scratch_that": True,
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen3.5",
    "lmstudio_url": "http://localhost:1234",
    "lmstudio_model": "",
    "openai_api_key": "",
    "openai_model": "",
    "openrouter_api_key": "",
    "openrouter_model": "",
    "custom_llm_url": "",
    "custom_llm_api_key": "",
    "custom_llm_model": "",
    "custom_llm_format": "openai",
    "post_processor_ollama_model": "",
    "post_processor_openai_model": "",
    "post_processor_openrouter_model": "",
    "post_processor_lmstudio_model": "",
    "post_processor_custom_llm_model": "",

    # ---- quill (speak an instruction to rewrite selected text) ----
    "enable_quill": False,
    "quill_hotkey": {"label": "Right Ctrl", "vk": VK["Right Ctrl"]},
    "quill_backend": "ollama",
    "quill_model": "",
    "quill_system_prompt": (
        "You rewrite text according to a spoken instruction. Output only the rewritten "
        "text with no preamble, commentary, quotes or code fences. If there is no text to "
        "rewrite, write new text that fulfils the instruction."
    ),

    # ---- meetings ----
    "auto_record_meetings": False,
    "enable_meeting_recording_hotkey": False,
    "meeting_recording_hotkey": {"label": "Ctrl+Shift+R", "vk": 0x52, "modifiers": ["ctrl", "shift"]},
    "meeting_recording_hotkey_trigger_threshold_ms": 600,
    "meeting_recording_save_policy": "never",   # never | always | on_error
    "meeting_recording_file_format": "wav",
    "meeting_transcription_backend": "whisper",
    "meeting_transcription_model": "large-v3",
    "meeting_live_caption_backend": "parakeet_realtime",
    "meeting_summary_backend": "openrouter",
    "meeting_summary_model": "",
    "meeting_summary_retry_count": 3,
    "default_meeting_template_id": "auto",
    "meeting_chunk_seconds": 20,
    "meeting_hook_enabled": False,
    "meeting_hook_path": "",
    "meeting_hook_timeout_seconds": 30,
    "auto_export_markdown_enabled": False,
    "auto_export_file_format": "markdown",
    "auto_export_markdown_content": "notes",
    "win_meeting_detection_enabled": False,
    "win_meeting_detection_processes": [
        "Zoom.exe", "Teams.exe", "ms-teams.exe", "chrome.exe", "msedge.exe",
        "firefox.exe", "slack.exe", "WhatsApp.exe", "Webex.exe", "Discord.exe",
    ],

    # ---- interface ----
    "dark_mode": False,
    "show_floating_indicator": True,
    "show_hotkey_on_floating_indicator": False,
    "show_hotkey_in_menu_bar": True,
    "recording_color_hex": "1e1e2e",
    "menu_bar_icon": "pencil.line",
    "launch_at_login": True,
    "open_dashboard_on_launch": True,
    "indicator_position": None,            # [x, y] or None -> bottom-centre of primary
    "icloud_sync_enabled": False,          # accepted and ignored on Windows

    # ---- windows-only ----
    "win_audio_input_device": "",          # "" = system default
    "win_loopback_device": "",             # "" = default render device loopback
    "win_compute_device": "auto",          # auto | cuda | cpu
    "win_compute_type": "auto",            # auto | float16 | int8_float16 | int8
    "win_inject_method": "auto",           # auto | sendinput | clipboard
    "win_clipboard_restore_delay_ms": 400,
    "win_suppress_hotkey_passthrough": True,
    "win_altgr_guard": True,               # warn/auto-rebind if Right Alt is AltGr
    "win_single_instance": True,
    "win_log_level": "INFO",
}

# Never let an incoming (e.g. Mac-exported) config blank out local credentials.
PROTECTED_KEYS = (
    "openai_api_key", "openrouter_api_key", "custom_llm_api_key", "custom_llm_url",
)


def _normalise_hotkey(hk: Any, default: dict) -> dict:
    """Give every hotkey a Windows `vk`, deriving it from Mac fields if needed."""
    if not isinstance(hk, dict):
        return copy.deepcopy(default)
    out = dict(hk)
    if not isinstance(out.get("vk"), int):
        label = str(out.get("label", "")).strip()
        # A Mac label maps cleanly: "Right Option" -> Right Alt, "Right Cmd" -> Right Win.
        alias = {"Right Option": "Right Alt", "Left Option": "Left Alt",
                 "Right Cmd": "Right Win", "Left Cmd": "Left Win",
                 "Right Command": "Right Win", "Left Command": "Left Win",
                 "Right Control": "Right Ctrl", "Left Control": "Left Ctrl"}
        label = alias.get(label, label)
        if label in VK:
            out["vk"] = VK[label]
            out["label"] = label
        elif isinstance(out.get("keyCode"), int) and out["keyCode"] in MAC_KEYCODE_TO_VK:
            out["vk"] = MAC_KEYCODE_TO_VK[out["keyCode"]]
            out["label"] = VK_NAME.get(out["vk"], out.get("label", "?"))
        else:
            return copy.deepcopy(default)
    if not out.get("label"):
        out["label"] = VK_NAME.get(out["vk"], f"VK 0x{out['vk']:02X}")
    return out


class Config:
    """Thread-safe dict-backed config with atomic save."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.config_path()
        self._lock = threading.RLock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._listeners: list[Any] = []
        self.imported_from_mac = False

    # -- io ----------------------------------------------------------------
    def load(self) -> Config:
        with self._lock:
            self._data = copy.deepcopy(DEFAULTS)
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        self._data.update(raw)
                    else:
                        log.warning("config.json is not an object; using defaults")
                except (json.JSONDecodeError, OSError) as exc:
                    log.error("could not read %s (%s); using defaults", self.path, exc)
                    self._quarantine()
            self._migrate()
        return self

    def _quarantine(self) -> None:
        try:
            bad = self.path.with_suffix(".json.corrupt")
            self.path.replace(bad)
            log.warning("moved unreadable config to %s", bad)
        except OSError:
            pass

    def _migrate(self) -> None:
        for key in ("dictation_hotkey", "quill_hotkey", "meeting_recording_hotkey"):
            self._data[key] = _normalise_hotkey(self._data.get(key), DEFAULTS[key])
        # A Mac config names engines and CoreML model ids we cannot load.
        for backend_key, model_key in (("stt_backend", "stt_model"),
                                       ("meeting_transcription_backend",
                                        "meeting_transcription_model")):
            backend = self._data.get(backend_key, "whisper")
            model = str(self._data.get(model_key, ""))
            new_backend, new_model, remapped = map_backend(backend, model)
            if remapped:
                log.info("%s %r is macOS-only; using %s/%s on Windows",
                         backend_key, backend, new_backend, new_model or "(default)")
                self.imported_from_mac = True
            self._data[backend_key] = new_backend
            model = new_model
            if new_backend == "whisper" and (
                    "coreml" in model.lower() or "/" in model or model.endswith("MB")
                    or not model):
                model = _mac_model_to_windows(model)
            elif new_backend == "parakeet" and "/" in model:
                model = model.split("/")[-1].replace("-coreml", "")
            self._data[model_key] = model

        wm = str(self._data.get("whisper_model", ""))
        if "/" in wm:                       # e.g. FluidInference/parakeet-tdt-0.6b-v3-coreml
            self._data["whisper_model"] = wm.split("/")[-1].replace("-coreml", "")
        cw = self._data.get("custom_words")
        if not isinstance(cw, list):
            self._data["custom_words"] = []

    def save(self) -> None:
        with self._lock:
            data = copy.deepcopy(self._data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)              # atomic on NTFS

    def merge_file(self, other: Path) -> int:
        """Merge an exported config in without clobbering local credentials."""
        incoming = json.loads(Path(other).read_text(encoding="utf-8"))
        for k in PROTECTED_KEYS:
            incoming.pop(k, None)
        with self._lock:
            self._data.update(incoming)
            self._migrate()
        self.save()
        return len(incoming)

    # -- access ------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return self._data[key]
        return DEFAULTS.get(key, default)

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        with self._lock:
            if key.endswith("_hotkey"):
                value = _normalise_hotkey(value, DEFAULTS.get(key, {}))
            self._data[key] = value
        if save:
            self.save()
        self._notify(key, value)

    def update(self, values: dict[str, Any], *, save: bool = True) -> None:
        for k, v in values.items():
            self.set(k, v, save=False)
        if save:
            self.save()

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def redacted(self) -> dict[str, Any]:
        d = self.as_dict()
        for k in d:
            if "api_key" in k and d[k]:
                d[k] = "***"
        return d

    # -- change notification ----------------------------------------------
    def subscribe(self, fn: Any) -> None:
        self._listeners.append(fn)

    def _notify(self, key: str, value: Any) -> None:
        for fn in list(self._listeners):
            try:
                fn(key, value)
            except Exception:                       # a bad listener must not break saves
                log.exception("config listener failed for %s", key)


# macOS Muesli ships engines with no Windows build (Apple Speech, Cohere
# Transcribe, SenseVoice, Nemotron, Bodhan, Gemma). A config copied from a Mac
# therefore names a backend that cannot load here. Mapping each to the closest
# Windows engine keeps the imported config working instead of silently dropping
# to a small fallback model.
MAC_BACKEND_MAP = {
    "apple_speech": ("whisper", "large-v3"),
    "applespeech": ("whisper", "large-v3"),
    "cohere": ("whisper", "large-v3"),
    "sensevoice": ("whisper", "large-v3"),
    "qwen3asr": ("whisper", "large-v3"),
    "qwen3_asr": ("whisper", "large-v3"),
    "nemotron35": ("whisper", "large-v3"),
    "nemotron": ("whisper", "large-v3"),
    "bodhan": ("whisper", "large-v3"),      # Indic: whisper large-v3 is multilingual
    "bodhan_core": ("whisper", "large-v3"),
    "bodhan_flex": ("whisper", "large-v3"),
    "gemma": ("whisper", "large-v3"),
    "parakeet_realtime_eou": ("parakeet", "parakeet-tdt-0.6b-v3"),
    "parakeet": ("parakeet", "parakeet-tdt-0.6b-v3"),
    "whisper": ("whisper", ""),
}

WINDOWS_BACKENDS = ("whisper", "parakeet", "openai", "openrouter", "groq")


def map_backend(backend: str, model: str) -> tuple[str, str, bool]:
    """-> (backend, model, was_remapped)"""
    b = str(backend or "").strip().lower()
    if b in WINDOWS_BACKENDS:
        return b, model, False
    mapped = MAC_BACKEND_MAP.get(b)
    if not mapped:
        log.warning("unknown STT backend %r; using whisper", backend)
        return "whisper", "large-v3", True
    new_backend, new_model = mapped
    return new_backend, (new_model or model), True


def _mac_model_to_windows(mac_model: str) -> str:
    m = mac_model.lower()
    for needle, win in (
        ("large-v3-turbo", "large-v3-turbo"), ("large-v3", "large-v3"),
        ("large", "large-v3"), ("medium", "medium"), ("small", "small"),
        ("base", "base"), ("tiny", "tiny"),
    ):
        if needle in m:
            return win
    return "large-v3"
