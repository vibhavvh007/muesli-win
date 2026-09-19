"""Meeting recorder: mic ("You") + system audio ("Others") in parallel.

Muesli transcribes both sides by tapping CoreAudio; the Windows equivalent is a
WASAPI loopback capture of the render device. Both streams are chunked on
silence so each chunk lands on a natural speech boundary rather than mid-word,
then transcribed independently and merged on timestamp.

If loopback is unavailable (no pyaudiowpatch, or a machine with no render
device) the meeting still records your side rather than failing outright, and
says so.
"""
from __future__ import annotations

import logging
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .. import paths
from ..audio.capture import TARGET_RATE, LoopbackSource, MicSource

log = logging.getLogger(__name__)

SILENCE_LEVEL = 0.01
MIN_CHUNK_S = 4.0
MAX_CHUNK_S = 25.0
SILENCE_HOLD_S = 0.7


@dataclass
class Chunk:
    source: str          # 'you' | 'others'
    start_ms: int
    end_ms: int
    audio: np.ndarray = field(repr=False)


class _StreamChunker:
    """Splits one stream into utterance-sized chunks on silence."""

    def __init__(self, source: str, emit: Callable[[Chunk], None]) -> None:
        self.source = source
        self.emit = emit
        self._buf: list[np.ndarray] = []
        self._buffered = 0
        self._silence_run = 0.0
        self._t0_ms = 0
        self._cursor_ms = 0

    def feed(self, block: np.ndarray) -> None:
        if block.size == 0:
            return
        dur_s = len(block) / TARGET_RATE
        loud = float(np.abs(block).max()) > SILENCE_LEVEL

        if not self._buf and not loud:
            self._cursor_ms += int(dur_s * 1000)         # drop leading silence
            return
        if not self._buf:
            self._t0_ms = self._cursor_ms

        self._buf.append(block)
        self._buffered += len(block)
        self._cursor_ms += int(dur_s * 1000)
        self._silence_run = 0.0 if loud else self._silence_run + dur_s

        buffered_s = self._buffered / TARGET_RATE
        if buffered_s >= MAX_CHUNK_S or (
                buffered_s >= MIN_CHUNK_S and self._silence_run >= SILENCE_HOLD_S):
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        audio = np.concatenate(self._buf)
        chunk = Chunk(self.source, self._t0_ms, self._cursor_ms, audio)
        self._buf, self._buffered, self._silence_run = [], 0, 0.0
        if float(np.abs(audio).max()) > SILENCE_LEVEL:    # never transcribe pure silence
            self.emit(chunk)


class MeetingRecorder:
    def __init__(self, cfg, store, transcriber, *,
                 on_segment: Callable[[str, str], None] | None = None,
                 on_error: Callable[[str], None] | None = None,
                 on_notice: Callable[[str], None] | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.transcriber = transcriber
        self.on_segment = on_segment
        self.on_error = on_error
        self.on_notice = on_notice

        self.meeting_id: str | None = None
        self._mic: MicSource | None = None
        self._loop: LoopbackSource | None = None
        self._chunkers: dict[str, _StreamChunker] = {}
        self._pending: list[Chunk] = []
        self._pending_lock = threading.Lock()
        self._running = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started_at = 0.0
        self._keep_audio: dict[str, list[np.ndarray]] = {"you": [], "others": []}
        self._save_audio = cfg.get("meeting_recording_save_policy", "never") != "never"

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._started_at else 0.0

    # -- lifecycle ---------------------------------------------------------
    def start(self, title: str = "") -> str | None:
        if self._running.is_set():
            return self.meeting_id
        self.meeting_id = self.store.create_meeting(title)
        self._chunkers = {
            "you": _StreamChunker("you", self._queue),
            "others": _StreamChunker("others", self._queue),
        }

        self._mic = MicSource(self.cfg.get("win_audio_input_device", ""),
                              on_error=self._err)
        mic_ok = self._mic.start()
        if not mic_ok:
            self._mic = None

        self._loop = LoopbackSource(self.cfg.get("win_loopback_device", ""),
                                    on_error=lambda m: log.warning("loopback: %s", m))
        loop_ok = self._loop.start()
        if not loop_ok:
            self._loop = None
            self._notice("System audio could not be captured - recording your side only. "
                         "Install pyaudiowpatch for the other side of the call.")

        if not mic_ok and not loop_ok:
            self._err("No audio source available; meeting not started.")
            self.store.delete_meeting(self.meeting_id)
            self.meeting_id = None
            return None

        self._running.set()
        self._started_at = time.monotonic()
        for name, fn in (("meeting-pump", self._pump_loop),
                         ("meeting-stt", self._stt_loop)):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        log.info("meeting %s started (mic=%s loopback=%s)", self.meeting_id, mic_ok, loop_ok)
        return self.meeting_id

    def stop(self) -> str | None:
        if not self._running.is_set():
            return None
        self._running.clear()
        for src in (self._mic, self._loop):
            if src is not None:
                try:
                    src.stop()
                except Exception:
                    log.exception("error stopping audio source")
        for c in self._chunkers.values():
            c.flush()
        for t in self._threads:
            t.join(timeout=20.0)
        self._threads = []
        self._drain_pending()                       # transcribe the tail

        mid = self.meeting_id
        if mid:
            segs = self.store.segments(mid)
            transcript = render_transcript(segs)
            audio_path = self._write_audio(mid) if self._save_audio else ""
            self.store.finish_meeting(mid, transcript=transcript,
                                      duration_ms=int(self.elapsed * 1000),
                                      audio_path=audio_path)
        self._mic = self._loop = None
        self._started_at = 0.0
        self.meeting_id = None
        return mid

    # -- internals ---------------------------------------------------------
    def _err(self, msg: str) -> None:
        log.error("meeting: %s", msg)
        if self.on_error:
            self.on_error(msg)

    def _notice(self, msg: str) -> None:
        log.info("meeting: %s", msg)
        if self.on_notice:
            self.on_notice(msg)

    def _queue(self, chunk: Chunk) -> None:
        with self._pending_lock:
            self._pending.append(chunk)

    def _pump_loop(self) -> None:
        while self._running.is_set():
            for src, key in ((self._mic, "you"), (self._loop, "others")):
                if src is None:
                    continue
                block = src.drain()
                if block.size:
                    self._chunkers[key].feed(block)
                    if self._save_audio:
                        self._keep_audio[key].append(block)
            time.sleep(0.05)

    def _stt_loop(self) -> None:
        while self._running.is_set():
            if not self._transcribe_one():
                time.sleep(0.15)

    def _drain_pending(self) -> None:
        while self._transcribe_one():
            pass

    def _transcribe_one(self) -> bool:
        with self._pending_lock:
            if not self._pending:
                return False
            chunk = self._pending.pop(0)
        try:
            result = self.transcriber.transcribe(chunk.audio)
        except Exception as exc:
            log.warning("meeting chunk transcription failed: %s", exc)
            return True
        text = (result.text or "").strip()
        if not text:
            return True
        speaker = "You" if chunk.source == "you" else "Others"
        try:
            self.store.add_segment(self.meeting_id, chunk.start_ms, chunk.end_ms,
                                   text, source=chunk.source, speaker=speaker)
        except Exception:
            log.exception("could not save meeting segment")
        if self.on_segment:
            try:
                self.on_segment(speaker, text)
            except Exception:
                log.exception("segment callback raised")
        return True

    def _write_audio(self, mid: str) -> str:
        try:
            parts = [np.concatenate(v) for v in self._keep_audio.values() if v]
            if not parts:
                return ""
            n = max(len(p) for p in parts)
            mixed = np.zeros(n, dtype=np.float32)
            for p in parts:
                mixed[: len(p)] += p
            mixed = np.clip(mixed / max(1, len(parts)), -1.0, 1.0)
            paths.recordings_dir().mkdir(parents=True, exist_ok=True)
            out = paths.recordings_dir() / f"{mid}.wav"
            with wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(TARGET_RATE)
                w.writeframes((mixed * 32767).astype(np.int16).tobytes())
            return str(out)
        except Exception:
            log.exception("could not write meeting audio")
            return ""
        finally:
            self._keep_audio = {"you": [], "others": []}


def render_transcript(segments: list[dict]) -> str:
    lines = []
    for s in segments:
        ts = _mmss(s["start_ms"])
        who = s.get("speaker") or ("You" if s.get("source") == "you" else "Others")
        lines.append(f"[{ts}] {who}: {s['text']}")
    return "\n".join(lines)


def _mmss(ms: int) -> str:
    total = int(ms / 1000)
    return f"{total // 60:02d}:{total % 60:02d}"
