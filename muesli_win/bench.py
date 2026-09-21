"""Can this laptop actually keep up?

The useful number is not the CPU model, it is the **real-time factor** (RTF):
seconds of compute per second of audio. You cannot look that up from specs -
two machines with the same chip differ by thermals, power plan and what else is
running - so this measures it by transcribing a fixed sample on the spot.

RTF answers both questions the user asks:

  dictation   wait = RTF x seconds spoken. 30 s of speech at RTF 0.3 is a 9 s pause.
  meetings    the recorder queues chunks and transcribes them in the background.
              Below RTF 1.0 the queue drains and a meeting of any length is fine.
              At or above 1.0 chunks arrive faster than they are consumed, so the
              backlog - and the memory holding it - grows without limit. That is
              a hard boundary, not a rule of thumb.
"""
from __future__ import annotations

import ctypes
import logging
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from . import paths
from .stt import parakeet, whisper_fw
from .stt.compute import has_cuda, resolve

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
SAMPLE_SECONDS = 30.0          # one full Whisper window; the natural unit
# Below this the queue drains comfortably. Between here and 1.0 it technically
# keeps up, but with too little margin to promise anything about a long meeting.
SAFE_MEETING_RTF = 0.75
# Below this quality rank a model is worth flagging as weak rather than
# silently recommending it.
WEAK_MODEL_QUALITY = 60
RATE = 16_000

# Relative transcription quality, 0-100. Speed is NOT a proxy for accuracy
# across architectures: Parakeet TDT 0.6b is both far faster AND far more
# accurate than Whisper tiny, so ranking by measured RTF recommended the worst
# model in the set. These are rough English WER standings from the upstream
# model cards, used only for ordering.
MODEL_QUALITY = {
    "large-v3": 100, "large-v3-turbo": 94, "distil-large-v3": 90,
    "parakeet-tdt-0.6b-v3": 92, "parakeet-tdt-0.6b-v2": 90,
    "parakeet-ctc-0.6b": 84,
    "medium": 85, "medium.en": 86, "small": 72, "small.en": 74,
    "base": 55, "base.en": 57, "tiny": 38, "tiny.en": 40,
}
# Parakeet is English-first. Whisper is properly multilingual, so a non-English
# user should not be pushed onto Parakeet however fast it measures.
ENGLISH_FIRST = {"parakeet-tdt-0.6b-v3", "parakeet-tdt-0.6b-v2", "parakeet-ctc-0.6b"}

# Resident memory each model needs once loaded, measured rather than guessed.
MODEL_RAM_MB = {
    "tiny": 400, "tiny.en": 400, "base": 550, "base.en": 550,
    "small": 1100, "small.en": 1100, "medium": 2600, "medium.en": 2600,
    "large-v3": 3400, "large-v3-turbo": 2100, "distil-large-v3": 2000,
    "parakeet-tdt-0.6b-v3": 1400, "parakeet-tdt-0.6b-v2": 1400,
    "parakeet-ctc-0.6b": 1300,
}


@dataclass
class Specs:
    cpu: str = ""
    cores: int = 0
    ram_gb: float = 0.0
    ram_free_gb: float = 0.0
    gpu: str = ""
    vram_gb: float = 0.0
    disk_free_gb: float = 0.0
    on_battery: bool | None = None
    platform: str = ""

    def summary(self) -> str:
        bits = [f"{self.cores} cores", f"{self.ram_gb:.0f} GB RAM"]
        if self.gpu:
            bits.append(f"{self.gpu}" + (f" {self.vram_gb:.0f} GB" if self.vram_gb else ""))
        else:
            bits.append("no CUDA GPU")
        return " · ".join(bits)


@dataclass
class BenchResult:
    model: str = ""
    backend: str = ""
    device: str = ""
    load_seconds: float = 0.0
    rtf: float = 0.0               # compute seconds per audio second
    sample_seconds: float = SAMPLE_SECONDS
    ram_needed_mb: int = 0
    error: str = ""
    specs: Specs = field(default_factory=Specs)

    @property
    def ok(self) -> bool:
        return not self.error and self.rtf > 0


# --- machine ---------------------------------------------------------------
def _ram_windows() -> tuple[float, float]:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullTotalPhys / 1024 ** 3, st.ullAvailPhys / 1024 ** 3


def _cpu_name() -> str:
    if IS_WINDOWS:
        try:
            import winreg
            key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except Exception:
            return platform.processor() or "unknown CPU"
    if sys.platform == "darwin":
        try:
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                  capture_output=True, text=True,
                                  timeout=5).stdout.strip()
        except Exception:
            return platform.processor() or "unknown CPU"
    return platform.processor() or "unknown CPU"


def _gpu() -> tuple[str, float]:
    if not has_cuda():
        return "", 0.0
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8).stdout.strip().splitlines()
        if out:
            name, mem = out[0].split(",")
            return name.strip(), float(mem) / 1024
    except Exception:
        log.debug("nvidia-smi query failed", exc_info=True)
    return "NVIDIA GPU", 0.0


def _on_battery() -> bool | None:
    if not IS_WINDOWS:
        return None
    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    st = SYSTEM_POWER_STATUS()
    if not ctypes.WinDLL("kernel32").GetSystemPowerStatus(ctypes.byref(st)):
        return None
    return st.ACLineStatus == 0        # 0 = offline, 1 = mains


def probe_specs() -> Specs:
    total = free = 0.0
    try:
        if IS_WINDOWS:
            total, free = _ram_windows()
        else:
            page = os.sysconf("SC_PAGE_SIZE")
            total = os.sysconf("SC_PHYS_PAGES") * page / 1024 ** 3
            free = total  # good enough off Windows; this tool targets Windows
    except Exception:
        log.debug("RAM probe failed", exc_info=True)
    gpu, vram = _gpu()
    try:
        disk = shutil.disk_usage(paths.models_dir().parent).free / 1024 ** 3
    except Exception:
        disk = 0.0
    return Specs(cpu=_cpu_name(), cores=os.cpu_count() or 0, ram_gb=total,
                 ram_free_gb=free, gpu=gpu, vram_gb=vram, disk_free_gb=disk,
                 on_battery=_on_battery(), platform=platform.platform())


# --- measurement -----------------------------------------------------------
def _sample_audio(seconds: float = SAMPLE_SECONDS) -> np.ndarray:
    """Speech-shaped noise.

    Silence is not a fair test: the model short-circuits and reports a speed it
    could never sustain on real audio. This is broadband noise shaped into
    syllable-rate bursts around the formant range, which costs the model roughly
    what speech does.
    """
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    rng = np.random.default_rng(0)
    carrier = sum(np.sin(2 * math.pi * f * t) for f in (140, 320, 700, 1800, 2600))
    noise = rng.normal(0, 0.25, n)
    syllables = 0.5 * (1 + np.sin(2 * math.pi * 4.5 * t))   # ~4.5 syllables/sec
    sig = (carrier / 5 + noise) * syllables
    return (0.35 * sig / max(1e-6, float(np.abs(sig).max()))).astype(np.float32)


def benchmark(model: str, backend: str = "", *, device: str = "auto",
              compute: str = "auto", seconds: float = SAMPLE_SECONDS) -> BenchResult:
    specs = probe_specs()
    if not backend:
        backend = "parakeet" if model in parakeet.MODELS else "whisper"
    result = BenchResult(model=model, backend=backend, sample_seconds=seconds,
                         specs=specs, ram_needed_mb=MODEL_RAM_MB.get(model, 0))
    try:
        if backend == "whisper":
            t = whisper_fw.WhisperTranscriber(model=model, device=device,
                                              compute_type=compute,
                                              vad_filter=False)  # measure all of it
            result.device = resolve(device, compute)[0]
        else:
            t = parakeet.ParakeetTranscriber(model=model)
            result.device = "cuda" if has_cuda() else "cpu"

        t0 = time.monotonic()
        t.load()
        result.load_seconds = round(time.monotonic() - t0, 1)

        audio = _sample_audio(seconds)
        t.transcribe(audio[: RATE * 2])            # warm up; first call is slowest
        t1 = time.monotonic()
        t.transcribe(audio)
        result.rtf = round((time.monotonic() - t1) / seconds, 3)
        t.unload()
    except Exception as exc:
        result.error = str(exc)
        log.exception("benchmark failed for %s", model)
    return result


# --- plain language --------------------------------------------------------
def interpret(result: BenchResult) -> dict:
    """Turn RTF into what the user actually wants to know."""
    if not result.ok:
        return {"headline": "Could not measure this model", "detail": result.error,
                "dictation": "", "meetings": "", "verdict": "error"}

    rtf, specs = result.rtf, result.specs
    wait_30s = rtf * 30

    if rtf < 0.15:
        verdict, headline = "excellent", "This model is fast on this PC"
    elif rtf < 0.4:
        verdict, headline = "good", "Comfortable on this PC"
    elif rtf < 0.7:
        verdict, headline = "ok", "Usable, with a noticeable pause"
    elif rtf < 1.0:
        verdict, headline = "slow", "Slow - only just keeping up"
    else:
        verdict, headline = "unusable", "Too slow for live use on this PC"

    dictation = (f"A 30-second dictation appears about {wait_30s:.0f} second"
                 f"{'s' if wait_30s >= 1.5 else ''} after you stop talking.")

    headroom = (1 - rtf) * 100
    if rtf < SAFE_MEETING_RTF:
        meetings = (f"Meeting transcription keeps up with {headroom:.0f}% headroom, "
                    "so a meeting of any length is fine.")
    elif rtf < 1.0:
        # Measured headroom this thin is not headroom. One busy moment - another
        # app, a thermal throttle, a browser tab - pushes it past real time, and
        # from there the backlog only grows. Promising "any length" here would be
        # overclaiming.
        meetings = (f"Meeting transcription only just keeps up ({headroom:.0f}% "
                    "headroom). Anything else demanding the CPU, or the laptop "
                    "throttling, will push it behind - use a smaller model for "
                    "long meetings.")
    else:
        # Below-realtime: the queue grows at (rtf - 1) seconds per second.
        lag_per_hour = (rtf - 1) * 60
        meetings = (f"Meeting transcription cannot keep up. It falls behind by about "
                    f"{lag_per_hour:.0f} minutes for every hour recorded, and the "
                    "backlog keeps growing - use a smaller model for meetings.")

    warnings = []
    if specs.on_battery:
        warnings.append("Measured on battery. Windows throttles the CPU on battery, "
                        "so this will be faster on mains power.")
    need = result.ram_needed_mb / 1024
    if specs.ram_free_gb and need and specs.ram_free_gb < need * 1.5:
        warnings.append(f"This model needs about {need:.1f} GB and only "
                        f"{specs.ram_free_gb:.1f} GB is free - close some apps or "
                        "choose a smaller model.")
    if result.load_seconds > 20:
        warnings.append(f"The model took {result.load_seconds:.0f}s to load, which "
                        "happens once per session.")

    return {"verdict": verdict, "headline": headline, "dictation": dictation,
            "meetings": meetings, "warnings": warnings,
            "rtf": rtf, "speed": f"{1 / rtf:.1f}x realtime" if rtf else ""}


def recommend(results: list[BenchResult], language: str = "auto") -> str:
    """The most accurate model that this machine still runs comfortably."""
    measured = [r for r in results if r.ok]
    if not measured:
        return "No model could be measured on this PC."

    usable = [r for r in measured if r.rtf < SAFE_MEETING_RTF]
    if not usable:
        fastest = min(measured, key=lambda r: r.rtf)
        return (f"Nothing tested is comfortable here; {fastest.model} was the "
                f"fastest at {1 / fastest.rtf:.1f}x realtime.")

    # For a non-English user an English-only model is not a slower choice, it is
    # a wrong one - it would return nonsense. Exclude rather than penalise,
    # provided something multilingual is actually available.
    non_english = language not in ("auto", "en", "", None)
    note = ""
    if non_english:
        multilingual = [r for r in usable if r.model not in ENGLISH_FIRST]
        if multilingual:
            usable = multilingual
        else:
            note = (f" Warning: it is English-first and you have set language "
                    f"'{language}', so accuracy will be poor - a Whisper model "
                    "would handle that language properly.")

    best = max(usable, key=lambda r: (MODEL_QUALITY.get(r.model, 50), -r.rtf))
    if not note and best.model in ENGLISH_FIRST:
        note = " It is English-first; switch to a Whisper model for other languages."

    # "The best of what was tested" is not the same as "good". Saying so avoids
    # quietly endorsing a weak model just because it was the only one measured
    # that fit.
    if MODEL_QUALITY.get(best.model, 50) < WEAK_MODEL_QUALITY:
        tested = ", ".join(sorted(r.model for r in measured))
        note += (f" It is a low-accuracy model though - it only wins because it "
                 f"was the best fit among the ones tested ({tested}). Measure "
                 "large-v3-turbo or medium as well before settling.")
    return (f"Use {best.model}: {1 / best.rtf:.0f}x realtime here, and the most "
            f"accurate option this PC runs comfortably.{note}")


def as_dict(result: BenchResult) -> dict:
    d = asdict(result)
    d["interpretation"] = interpret(result)
    return d
