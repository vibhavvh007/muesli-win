"""Audio capture.

Everything downstream wants the same thing: mono float32 at 16 kHz. Devices
rarely offer that, so each source opens at the device's native rate and
resamples once, here.

Two sources:
  MicSource       the user's microphone            ("You")
  LoopbackSource  whatever the speakers are playing ("Others"), via WASAPI
                  loopback - the Windows equivalent of Muesli's CoreAudio
                  process tap.

Both are pull-free: the device thread pushes into a thread-safe buffer and the
consumer drains it. A dropped callback is counted, never allowed to raise into
PortAudio.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

TARGET_RATE = 16_000
IS_WINDOWS = sys.platform == "win32"


# --- resampling -------------------------------------------------------------
def _resample(data: np.ndarray, src_rate: int, dst_rate: int = TARGET_RATE) -> np.ndarray:
    if src_rate == dst_rate or data.size == 0:
        return data.astype(np.float32, copy=False)
    try:
        import soxr  # high quality, tiny wheel
        return soxr.resample(data, src_rate, dst_rate).astype(np.float32, copy=False)
    except ImportError:
        pass
    # numpy fallback: low-pass then linear interpolate. Audibly worse than soxr
    # but keeps the app usable if the wheel is missing.
    if dst_rate < src_rate:
        decim = src_rate / dst_rate
        taps = int(8 * decim) | 1
        n = np.arange(taps) - (taps - 1) / 2
        cutoff = 0.5 / decim
        h = np.sinc(2 * cutoff * n) * np.hanning(taps)
        h /= h.sum()
        data = np.convolve(data, h, mode="same")
    n_out = int(round(len(data) * dst_rate / src_rate))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, num=len(data), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, data).astype(np.float32)


def _to_mono(block: np.ndarray) -> np.ndarray:
    if block.ndim == 1:
        return block
    return block.mean(axis=1)


# --- device discovery -------------------------------------------------------
@dataclass
class Device:
    index: int
    name: str
    channels: int
    rate: int
    loopback: bool = False
    default: bool = False

    def label(self) -> str:
        tag = " [loopback]" if self.loopback else ""
        star = " *" if self.default else ""
        return f"{self.name}{tag}{star}"


def _sd():
    import sounddevice as sd
    return sd


def list_input_devices() -> list[Device]:
    try:
        sd = _sd()
        default_in = sd.default.device[0] if sd.default.device else None
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                out.append(Device(i, d["name"], d["max_input_channels"],
                                  int(d["default_samplerate"]),
                                  default=(i == default_in)))
        return out
    except Exception:
        log.exception("could not enumerate input devices")
        return []


def list_loopback_devices() -> list[Device]:
    """WASAPI loopback endpoints (system audio). Windows only."""
    if not IS_WINDOWS:
        return []
    devices: list[Device] = []
    try:
        import pyaudiowpatch as pa
        p = pa.PyAudio()
        try:
            for info in p.get_loopback_device_info_generator():
                devices.append(Device(int(info["index"]), info["name"],
                                      int(info["maxInputChannels"]),
                                      int(info["defaultSampleRate"]), loopback=True))
            try:
                default_spk = p.get_default_wasapi_loopback()
                for d in devices:
                    d.default = (d.index == int(default_spk["index"]))
            except Exception:
                pass
        finally:
            p.terminate()
        return devices
    except ImportError:
        log.info("pyaudiowpatch not installed; trying sounddevice WASAPI loopback")
    except Exception:
        log.exception("pyaudiowpatch loopback enumeration failed")

    try:  # sounddevice >= 0.5 can mark an output device as a loopback input
        sd = _sd()
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_output_channels", 0) > 0 and "WASAPI" in sd.query_hostapis(
                    d["hostapi"])["name"]:
                devices.append(Device(i, d["name"], d["max_output_channels"],
                                      int(d["default_samplerate"]), loopback=True))
    except Exception:
        log.exception("sounddevice loopback enumeration failed")
    return devices


def resolve_device(name_or_empty: str, loopback: bool = False) -> Device | None:
    pool = list_loopback_devices() if loopback else list_input_devices()
    if not pool:
        return None
    if name_or_empty:
        for d in pool:
            if d.name == name_or_empty:
                return d
        for d in pool:                                   # tolerate a renamed device
            if name_or_empty.lower() in d.name.lower():
                return d
        log.warning("device %r not found; using default", name_or_empty)
    for d in pool:
        if d.default:
            return d
    return pool[0]


# --- sources ----------------------------------------------------------------
class _BaseSource:
    label = "base"

    def __init__(self, on_error: Callable[[str], None] | None = None) -> None:
        self._chunks: queue.Queue[np.ndarray] = queue.Queue()
        self._running = threading.Event()
        self._dropped = 0
        self._started_at = 0.0
        self.on_error = on_error
        self.src_rate = TARGET_RATE

    # -- lifecycle (subclasses implement _open/_close) --------------------
    def start(self) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @property
    def dropped_blocks(self) -> int:
        return self._dropped

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._started_at else 0.0

    # -- consumption -------------------------------------------------------
    def drain(self) -> np.ndarray:
        """Everything captured since the last drain, as 16 kHz mono float32."""
        parts = []
        while True:
            try:
                parts.append(self._chunks.get_nowait())
            except queue.Empty:
                break
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(parts)

    def _push(self, block: np.ndarray) -> None:
        try:
            mono = _to_mono(np.asarray(block, dtype=np.float32))
            self._chunks.put_nowait(_resample(mono, self.src_rate))
        except Exception:
            self._dropped += 1

    def _fail(self, msg: str) -> None:
        log.error("%s: %s", self.label, msg)
        if self.on_error:
            try:
                self.on_error(msg)
            except Exception:
                log.exception("audio error handler raised")


class MicSource(_BaseSource):
    label = "mic"

    def __init__(self, device_name: str = "", **kw) -> None:
        super().__init__(**kw)
        self.device_name = device_name
        self._stream = None
        self.device: Device | None = None

    def start(self) -> bool:
        if self._running.is_set():
            return True
        self.device = resolve_device(self.device_name, loopback=False)
        if self.device is None:
            self._fail("no microphone available")
            return False
        try:
            sd = _sd()
            self.src_rate = self.device.rate
            channels = min(self.device.channels, 2)

            def cb(indata, frames, time_info, status):
                if status:
                    self._dropped += 1
                self._push(indata.copy())

            self._stream = sd.InputStream(
                device=self.device.index, channels=channels, samplerate=self.src_rate,
                dtype="float32", blocksize=int(self.src_rate * 0.05), callback=cb,
            )
            self._stream.start()
            self._running.set()
            self._started_at = time.monotonic()
            log.info("mic open: %s @ %d Hz x%d", self.device.name, self.src_rate, channels)
            return True
        except Exception as exc:
            self._fail(f"could not open microphone ({exc})")
            self._stream = None
            return False

    def stop(self) -> None:
        self._running.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.exception("error closing mic stream")
            self._stream = None


class LoopbackSource(_BaseSource):
    """System audio. Needs pyaudiowpatch on Windows."""
    label = "loopback"

    def __init__(self, device_name: str = "", **kw) -> None:
        super().__init__(**kw)
        self.device_name = device_name
        self._pa = None
        self._stream = None
        self.device: Device | None = None

    def start(self) -> bool:
        if not IS_WINDOWS:
            self._fail("system-audio capture requires Windows")
            return False
        if self._running.is_set():
            return True
        self.device = resolve_device(self.device_name, loopback=True)
        if self.device is None:
            self._fail("no WASAPI loopback device found")
            return False
        try:
            import pyaudiowpatch as pa
            self._pa = pa.PyAudio()
            self.src_rate = self.device.rate
            channels = min(self.device.channels, 2)

            def cb(in_data, frame_count, time_info, status):
                buf = np.frombuffer(in_data, dtype=np.float32)
                if channels > 1:
                    buf = buf.reshape(-1, channels)
                self._push(buf)
                return (None, pa.paContinue)

            self._stream = self._pa.open(
                format=pa.paFloat32, channels=channels, rate=self.src_rate, input=True,
                frames_per_buffer=int(self.src_rate * 0.05),
                input_device_index=self.device.index, stream_callback=cb,
            )
            self._stream.start_stream()
            self._running.set()
            self._started_at = time.monotonic()
            log.info("loopback open: %s @ %d Hz x%d", self.device.name, self.src_rate,
                     channels)
            return True
        except ImportError:
            self._fail("pyaudiowpatch is not installed - system audio unavailable")
            return False
        except Exception as exc:
            self._fail(f"could not open loopback device ({exc})")
            self.stop()
            return False

    def stop(self) -> None:
        self._running.clear()
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            log.exception("error closing loopback stream")
        finally:
            self._stream = None
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass
        finally:
            self._pa = None


@dataclass
class Recording:
    """Accumulates a take and reports level for the UI."""
    samples: list[np.ndarray] = field(default_factory=list)
    _peak: float = 0.0

    def add(self, block: np.ndarray) -> None:
        if block.size:
            self.samples.append(block)
            self._peak = max(self._peak * 0.85, float(np.abs(block).max()))

    @property
    def level(self) -> float:
        return min(1.0, self._peak * 1.8)

    @property
    def duration(self) -> float:
        return sum(len(s) for s in self.samples) / TARGET_RATE

    def audio(self) -> np.ndarray:
        if not self.samples:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.samples)

    def clear(self) -> None:
        self.samples.clear()
        self._peak = 0.0
