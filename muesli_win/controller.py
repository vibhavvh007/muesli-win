"""Dictation controller: hotkey -> record -> transcribe -> clean -> type.

Deliberately UI-free. It reports what it is doing through `Events` callbacks so
the Qt layer, the CLI and the tests can all drive the same object.

Concurrency contract:
  * hotkey actions arrive on the hook's dispatch thread
  * capture runs on the audio device thread
  * transcription runs on a dedicated worker, one take at a time
  * nothing here touches Qt
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .audio.capture import TARGET_RATE, MicSource, Recording
from .audio.sounds import Sounds
from .audio.sysaudio import make_muter
from .stt.registry import LazyTranscriber
from .text import postprocess as pp
from .text.dictionary import Dictionary
from .text.llm import LLMError, chat, target_from_config
from .winput.hook import KeyboardHook, layout_has_altgr
from .winput.injector import (
    TextInjector,
    diagnose_injection_failure,
    foreground_window_info,
)
from .winput.selection import read_selection
from .winput.statemachine import Action, HotkeyStateMachine

log = logging.getLogger(__name__)

# How long an error stays on the floating indicator before it clears itself.
# The tray notification keeps the detail, so the pill does not need to linger.
ERROR_DISMISS_SECONDS = 4.0
# If transcription has not finished by now something is wrong; release the UI
# rather than leaving "Transcribing" on screen indefinitely.
TRANSCRIBE_WATCHDOG_SECONDS = 180.0


@dataclass
class Events:
    """Everything the UI can react to. All are optional."""
    on_state: Callable[[str], None] | None = None          # idle|listening|thinking|error
    on_level: Callable[[float], None] | None = None
    on_partial: Callable[[str], None] | None = None
    on_text: Callable[[str], None] | None = None
    on_error: Callable[[str], None] | None = None
    on_notice: Callable[[str], None] | None = None

    def fire(self, name: str, *args) -> None:
        fn = getattr(self, name, None)
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            log.exception("event handler %s raised", name)


class DictationController:
    def __init__(self, cfg, store, events: Events | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.events = events or Events()

        self.machine = HotkeyStateMachine(
            threshold_ms=int(cfg.get("hotkey_trigger_threshold_ms", 250)),
            double_tap_ms=int(cfg.get("double_tap_window_ms", 400)),
            double_tap_enabled=bool(cfg.get("enable_double_tap_dictation", True)),
            suppress_key=bool(cfg.get("win_suppress_hotkey_passthrough", True)),
        )
        self.hook = KeyboardHook(self.machine, self._hotkey_vk(), self._on_action,
                                 hotkey_modifiers=self._hotkey_mods())
        self.transcriber = LazyTranscriber(cfg, purpose="dictation")
        self.injector = TextInjector(
            method=cfg.get("win_inject_method", "auto"),
            clipboard_restore_delay_ms=int(cfg.get("win_clipboard_restore_delay_ms", 400)),
        )
        self.sounds = Sounds(bool(cfg.get("sound_enabled", True)))
        self.dictionary = Dictionary(cfg.get("custom_words", []))

        self._mic: MicSource | None = None
        self._muter = None
        self._rec = Recording()
        self._pump: threading.Thread | None = None
        self._pumping = threading.Event()
        self._busy = threading.Lock()
        self._started_at = 0.0
        self._last_voice_at = 0.0
        self._target_app: tuple[str, str] = ("", "")
        self._state = "idle"
        self._state_timer: threading.Timer | None = None
        self._quill_mode = False
        self._quill_selection = ""

        cfg.subscribe(self._on_config_change)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> bool:
        self.transcriber.preload()
        ok = self.hook.start()
        if not ok:
            self.events.fire("on_error", "Could not install the global hotkey. "
                                          "Another app may be blocking it.")
        elif self._altgr_conflict():
            self.events.fire(
                "on_notice",
                "Right Alt is AltGr on your keyboard layout. Muesli is holding that key, "
                "so AltGr characters (@ \\ € …) will stop working. Pick Right Ctrl or F13 "
                "in Settings > Hotkey.",
            )
        return ok

    def stop(self) -> None:
        self._cancel_state_timer()
        self._stop_capture(discard=True)
        self.hook.stop()

    def _hotkey_vk(self) -> int:
        hk = self.cfg.get("dictation_hotkey", {})
        return int(hk.get("vk", 0xA5))

    def _hotkey_mods(self) -> set[str]:
        hk = self.cfg.get("dictation_hotkey", {}) or {}
        mods = hk.get("modifiers") or []
        return {str(m).lower() for m in mods if str(m).lower() in
                ("ctrl", "alt", "shift", "win")}

    def _altgr_conflict(self) -> bool:
        # Only a BARE Right Alt swallows AltGr. As part of a combination the key
        # still reaches the layout normally, so there is nothing to warn about.
        return (self.cfg.get("win_altgr_guard", True)
                and self._hotkey_vk() == 0xA5
                and not self._hotkey_mods()
                and self.cfg.get("win_suppress_hotkey_passthrough", True)
                and layout_has_altgr())

    def _on_config_change(self, key: str, value) -> None:
        if key == "custom_words":
            self.dictionary = Dictionary(value or [])
        elif key == "dictation_hotkey":
            self.hook.set_hotkey(int((value or {}).get("vk", 0xA5)),
                                 self._hotkey_mods())
        elif key in ("stt_backend", "stt_model", "whisper_language", "whisper_model",
                     "win_compute_device", "win_compute_type"):
            self.transcriber.reload()
        elif key == "sound_enabled":
            self.sounds.enabled = bool(value)
        elif key == "hotkey_trigger_threshold_ms":
            self.machine.threshold_ms = int(value)
        elif key == "enable_double_tap_dictation":
            self.machine.double_tap_enabled = bool(value)
        elif key == "win_suppress_hotkey_passthrough":
            self.machine.suppress_key = bool(value)
        elif key == "win_inject_method":
            self.injector.method = value

    # -- state -------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        """Change state and guarantee it is not terminal.

        `error` used to stick: nothing returned it to idle, so the floating
        indicator sat there until the next dictation. `thinking` could stick too
        if anything after the transcribe call raised. Both now clear themselves.
        """
        self._cancel_state_timer()
        if state != self._state:
            self._state = state
            self.events.fire("on_state", state)

        if state == "error":
            self._arm_state_timer(ERROR_DISMISS_SECONDS, self._clear_error)
        elif state == "thinking":
            self._arm_state_timer(TRANSCRIBE_WATCHDOG_SECONDS, self._watchdog_fired)

    def _arm_state_timer(self, seconds: float, fn) -> None:
        t = threading.Timer(seconds, fn)
        t.daemon = True
        self._state_timer = t
        t.start()

    def _cancel_state_timer(self) -> None:
        t = getattr(self, "_state_timer", None)
        if t is not None:
            t.cancel()
        self._state_timer = None

    def _clear_error(self) -> None:
        # Only if nothing has started since; the tray notification carries the
        # detail, so the indicator does not need to sit there accusing.
        if self._state == "error" and not self.machine.recording:
            self._set_state("idle")

    def _watchdog_fired(self) -> None:
        if self._state != "thinking":
            return
        log.error("transcription still running after %ss; releasing the UI",
                  TRANSCRIBE_WATCHDOG_SECONDS)
        self.events.fire(
            "on_error",
            f"Transcription is taking longer than {int(TRANSCRIBE_WATCHDOG_SECONDS)}s. "
            "It may still finish - check the dashboard. If this keeps happening, "
            "try a smaller model.")
        self._set_state("idle")

    @property
    def state(self) -> str:
        return self._state

    @property
    def recording(self) -> bool:
        return self.machine.recording

    # -- hotkey actions (hook dispatch thread) -----------------------------
    def _on_action(self, action: Action, source: str) -> None:
        if action is Action.START_HOLD:
            self._start_capture(mode="hold")
        elif action is Action.START_HANDSFREE:
            self._start_capture(mode="handsfree")
        elif action is Action.STOP:
            self._stop_capture(discard=False)
        elif action is Action.CANCEL:
            self._stop_capture(discard=True)

    def toggle(self) -> None:
        """Tray / CLI entry point."""
        if self.machine.recording:
            d = self.machine.force_stop()
            if d.action is Action.STOP:
                self._stop_capture(discard=False)
        else:
            self.machine.state = self.machine.state.__class__.HANDSFREE
            self._start_capture(mode="handsfree")

    def cancel(self) -> None:
        d = self.machine.escape(int(time.monotonic() * 1000))
        if d.action is Action.CANCEL:
            self._stop_capture(discard=True)

    # -- capture -----------------------------------------------------------
    def _start_capture(self, mode: str) -> None:
        if self._mic is not None:
            return
        self._target_app = foreground_window_info()
        self._rec = Recording()
        self._mic = MicSource(self.cfg.get("win_audio_input_device", ""),
                              on_error=lambda m: self.events.fire("on_error", m))
        if not self._mic.start():
            self._mic = None
            self.machine.reset()
            self.sounds.play("error")
            self._set_state("error")
            return

        self._muter = make_muter(bool(self.cfg.get("mute_system_audio_during_dictation", True)))
        self._muter.mute()
        self._started_at = time.monotonic()
        self._last_voice_at = self._started_at
        self._set_state("listening")
        self.sounds.play("start")

        self._pumping.set()
        self._pump = threading.Thread(target=self._pump_loop, args=(mode,),
                                      name="dictation-pump", daemon=True)
        self._pump.start()

    def _pump_loop(self, mode: str) -> None:
        idle_timeout = float(self.cfg.get("idle_timeout", 120) or 0)
        max_seconds = float(self.cfg.get("max_dictation_seconds", 600) or 0)
        silence_floor = 0.012
        while self._pumping.is_set() and self._mic is not None:
            block = self._mic.drain()
            if block.size:
                self._rec.add(block)
                self.events.fire("on_level", self._rec.level)
                if float(np.abs(block).max()) > silence_floor:
                    self._last_voice_at = time.monotonic()
            now = time.monotonic()
            if max_seconds and (now - self._started_at) > max_seconds:
                self.events.fire("on_notice", "Dictation hit the maximum length and stopped.")
                self._external_stop()
                return
            if mode == "handsfree" and idle_timeout and (now - self._last_voice_at) > idle_timeout:
                self.events.fire("on_notice",
                                 f"Stopped after {int(idle_timeout)} s of silence.")
                self._external_stop()
                return
            time.sleep(0.05)

    def _external_stop(self) -> None:
        d = self.machine.force_stop()
        if d.action is Action.STOP:
            self._stop_capture(discard=False)

    def _stop_capture(self, *, discard: bool) -> None:
        self._pumping.clear()
        mic, self._mic = self._mic, None
        if mic is None:
            return
        try:
            self._rec.add(mic.drain())                 # whatever is still buffered
        finally:
            mic.stop()
        if self._muter is not None:
            self._muter.restore()
            self._muter = None

        audio = self._rec.audio()
        duration_ms = int(self._rec.duration * 1000)
        self._rec = Recording()

        if discard:
            self._quill_mode = False
            self._quill_selection = ""
            self._set_state("idle")
            self.sounds.play("cancel")
            return

        self.sounds.play("stop")
        if audio.size < TARGET_RATE * 0.25:            # under 250 ms is a stray press
            self._set_state("idle")
            return

        self._set_state("thinking")
        if self._quill_mode or self._quill_selection:
            self._quill_mode = False
            threading.Thread(target=self._process_quill, args=(audio,),
                             name="quill-transcribe", daemon=True).start()
            return
        threading.Thread(target=self._process, args=(audio, duration_ms),
                         name="dictation-transcribe", daemon=True).start()

    # -- transcription -----------------------------------------------------
    def _process(self, audio: np.ndarray, duration_ms: int) -> None:
        # try/finally around the whole body: anything raising after the
        # transcribe call used to skip the final _set_state("idle") and leave
        # "Transcribing" on screen permanently.
        try:
            self._process_inner(audio, duration_ms)
        except Exception as exc:
            log.exception("dictation processing failed")
            self.events.fire("on_error", f"Dictation failed: {exc}")
            self.sounds.play("error")
            self._set_state("error")
        finally:
            if self._state == "thinking":
                self._set_state("idle")

    def _process_inner(self, audio: np.ndarray, duration_ms: int) -> None:
        with self._busy:
            t0 = time.monotonic()
            try:
                result = self.transcriber.transcribe(
                    audio, language=self.cfg.get("whisper_language", "auto"))
            except Exception as exc:
                log.exception("transcription failed")
                self.events.fire("on_error", str(exc))
                self.sounds.play("error")
                self._set_state("error")
                return

            raw = result.text.strip()
            if not raw:
                self._set_state("idle")
                return

            text = pp.clean(
                raw, dictionary=self.dictionary,
                strip_filler=bool(self.cfg.get("strip_filler_words", True)),
                scratch_that=bool(self.cfg.get("enable_scratch_that", True)),
            )
            post_applied = False
            if self.cfg.get("enable_post_processor", False):
                cleaned = self._llm_cleanup(text)
                post_applied = cleaned != text
                text = cleaned

            self.events.fire("on_text", text)
            if not self.injector.inject(text):
                # Say what actually went wrong rather than assuming elevation,
                # and tell the user where their words are.
                self.events.fire("on_error", diagnose_injection_failure(
                    getattr(self.injector, "last_error", 0),
                    getattr(self.injector, "stranded_on_clipboard", False)))
            try:
                self.store.add_dictation(
                    raw_text=raw, text=text, duration_ms=duration_ms,
                    backend=result.backend, model=result.model, language=result.language,
                    app_name=self._target_app[0], window_title=self._target_app[1],
                    post_processed=post_applied,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
            except Exception:
                log.exception("could not save dictation to history")
            self._set_state("idle")

    # -- quill: speak an instruction, rewrite the selection -----------------
    def toggle_quill(self) -> None:
        """Quill hotkey. First press captures the selection and starts recording;
        second press transcribes the instruction and types the rewrite over it.

        The selection has to be read at the first press: by the time the user
        has finished speaking it is still selected, so injecting the result
        replaces it, which is exactly the behaviour wanted.
        """
        if not self.cfg.get("enable_quill", False):
            return
        if self._quill_mode:
            self._quill_mode = False
            self._stop_capture(discard=False)
            return
        if self._mic is not None:
            return                                  # a dictation is already running
        self._quill_selection = read_selection(self.injector)
        self._quill_mode = True
        self.machine.state = type(self.machine.state).HANDSFREE
        self._start_capture(mode="handsfree")
        self.events.fire(
            "on_notice",
            "Quill: say how to rewrite the selection, then press the key again."
            if self._quill_selection else
            "Quill: nothing selected - say what you want written.")

    def _process_quill(self, audio: np.ndarray) -> None:
        try:
            self._process_quill_inner(audio)
        except Exception as exc:
            log.exception("quill failed")
            self.events.fire("on_error", f"Quill failed: {exc}")
            self._set_state("error")
        finally:
            if self._state == "thinking":
                self._set_state("idle")

    def _process_quill_inner(self, audio: np.ndarray) -> None:
        selection, self._quill_selection = self._quill_selection, ""
        try:
            result = self.transcriber.transcribe(
                audio, language=self.cfg.get("whisper_language", "auto"))
        except Exception as exc:
            self.events.fire("on_error", f"Quill: {exc}")
            self._set_state("error")
            return
        instruction = pp.clean(result.text, dictionary=self.dictionary)
        if not instruction.strip():
            self._set_state("idle")
            return
        try:
            target = target_from_config(self.cfg, "quill")
            user = (f"Instruction: {instruction}\n\n--- TEXT ---\n{selection}"
                    if selection else f"Instruction: {instruction}")
            out = chat(target, self.cfg.get("quill_system_prompt", ""), user,
                       temperature=0.3, timeout=120)
        except LLMError as exc:
            self.events.fire("on_error", f"Quill: {exc}")
            self._set_state("idle")
            return
        # Quill rewrites rather than cleans, so the length guard that protects
        # dictation must be relaxed here - the output can legitimately be longer.
        text = pp.sanitise_llm_output(out, selection or instruction, max_growth=40.0,
                                      dedupe=False)
        if text and not self.injector.inject(text):
            self.events.fire("on_error",
                             "Quill: could not type into the focused window.")
        self._set_state("idle")

    def _llm_cleanup(self, text: str) -> str:
        try:
            target = target_from_config(self.cfg, "post")
            out = chat(target, self.cfg.get("post_processor_system_prompt", ""), text,
                       temperature=0.0, timeout=45)
            return pp.sanitise_llm_output(out, text)
        except LLMError as exc:
            log.warning("post-processor unavailable: %s", exc)
            self.events.fire("on_notice", f"Post-processing skipped: {exc}")
            return text
        except Exception:
            log.exception("post-processor failed")
            return text
