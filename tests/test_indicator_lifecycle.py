"""The floating indicator must always come back down.

Reported: the small "Transcribing" pill kept showing up and staying there. Three
causes, each covered below:

  * `error` was a terminal state - nothing ever returned it to idle
  * only the transcribe call sat inside try/except in _process, so anything
    raising afterwards skipped the final _set_state("idle")
  * a slow or hung transcription had no timeout at all
"""
import os
import time

import numpy as np
import pytest

from muesli_win import controller as ctl_mod
from muesli_win.config import Config
from muesli_win.controller import DictationController, Events
from muesli_win.storage.db import Store
from muesli_win.stt.base import Transcript

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeTranscriber:
    def __init__(self, text="hello", boom=None):
        self.text, self.boom = text, boom

    def transcribe(self, audio, **kw):
        if self.boom:
            raise self.boom
        return Transcript(text=self.text, backend="fake", model="fake")


class FakeInjector:
    def __init__(self, ok=True, boom=None):
        self.ok, self.boom, self.method = ok, boom, "auto"
        self.typed = []

    def inject(self, text):
        if self.boom:
            raise self.boom
        self.typed.append(text)
        return self.ok


@pytest.fixture
def ctl(tmp_path):
    cfg = Config(tmp_path / "c.json").load()
    c = DictationController(cfg, Store(tmp_path / "t.db"), Events())
    yield c
    c._cancel_state_timer()


AUDIO = np.zeros(16_000, dtype=np.float32)


# --- the stuck "Transcribing" pill -------------------------------------------
def test_state_returns_to_idle_when_injection_raises(ctl):
    """The reported bug: an exception after transcribe left it on 'thinking'."""
    ctl.transcriber = FakeTranscriber()
    ctl.injector = FakeInjector(boom=RuntimeError("clipboard exploded"))
    ctl._process(AUDIO, 1000)
    assert ctl.state != "thinking", "the indicator would be stuck on Transcribing"


def test_state_returns_to_idle_when_cleanup_raises(ctl, monkeypatch):
    ctl.transcriber = FakeTranscriber()
    ctl.injector = FakeInjector()
    monkeypatch.setattr(ctl_mod.pp, "clean",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad regex")))
    ctl._process(AUDIO, 1000)
    assert ctl.state != "thinking"


def test_a_normal_dictation_ends_idle(ctl):
    ctl.transcriber = FakeTranscriber()
    ctl.injector = FakeInjector()
    ctl._process(AUDIO, 1000)
    assert ctl.state == "idle"


def test_empty_transcript_ends_idle(ctl):
    ctl.transcriber = FakeTranscriber(text="   ")
    ctl.injector = FakeInjector()
    ctl._process(AUDIO, 1000)
    assert ctl.state == "idle"


# --- the stuck "Error" pill ---------------------------------------------------
def test_error_clears_itself(ctl, monkeypatch):
    monkeypatch.setattr(ctl_mod, "ERROR_DISMISS_SECONDS", 0.05)
    ctl.transcriber = FakeTranscriber(boom=RuntimeError("model gone"))
    ctl.injector = FakeInjector()
    ctl._process(AUDIO, 1000)
    assert ctl.state == "error"
    time.sleep(0.25)
    assert ctl.state == "idle", "error was terminal - the pill never went away"


def test_error_does_not_clear_while_recording_again(ctl, monkeypatch):
    monkeypatch.setattr(ctl_mod, "ERROR_DISMISS_SECONDS", 0.05)
    ctl._set_state("error")
    ctl.machine.state = type(ctl.machine.state).HANDSFREE   # user started again
    time.sleep(0.2)
    assert ctl.state == "error"      # the new session owns the indicator now


# --- the hung transcription ---------------------------------------------------
def test_watchdog_releases_a_hung_transcription(ctl, monkeypatch):
    monkeypatch.setattr(ctl_mod, "TRANSCRIBE_WATCHDOG_SECONDS", 0.05)
    seen = []
    ctl.events = Events(on_error=seen.append)
    ctl._set_state("thinking")
    time.sleep(0.25)
    assert ctl.state == "idle"
    assert seen and "longer than" in seen[0]


def test_watchdog_does_not_fire_once_finished(ctl, monkeypatch):
    monkeypatch.setattr(ctl_mod, "TRANSCRIBE_WATCHDOG_SECONDS", 0.05)
    seen = []
    ctl.events = Events(on_error=seen.append)
    ctl._set_state("thinking")
    ctl._set_state("idle")
    time.sleep(0.2)
    assert seen == []


# --- the widget itself --------------------------------------------------------
@pytest.fixture
def indicator(tmp_path):
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        QApplication([])
    from muesli_win.ui.indicator import FloatingIndicator
    return FloatingIndicator(Config(tmp_path / "c.json").load())


def test_indicator_hides_on_idle(indicator):
    indicator.set_state("listening")
    assert indicator.isVisible()
    indicator.set_state("idle")
    assert not indicator.isVisible()


def test_indicator_respects_the_setting_even_while_showing(indicator):
    indicator.set_state("listening")
    assert indicator.isVisible()
    indicator.cfg.set("show_floating_indicator", False)
    indicator.set_state("thinking")
    assert not indicator.isVisible(), "turning the setting off must take it away"


def test_level_meter_only_animates_while_listening(indicator):
    indicator.set_state("listening")
    assert indicator._decay.isActive()
    indicator.set_state("thinking")
    assert not indicator._decay.isActive(), "no point animating behind a static pill"
    indicator.set_state("idle")
    assert not indicator._decay.isActive()
