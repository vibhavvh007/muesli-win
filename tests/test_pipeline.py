"""End-to-end dictation pipeline with the OS-specific parts faked.

Proves the chain that actually matters: audio -> transcript -> dictionary ->
voice commands -> filler strip -> tidy -> injected text -> history row.
"""

import numpy as np
import pytest

from muesli_win.config import Config
from muesli_win.controller import DictationController, Events
from muesli_win.storage.db import Store
from muesli_win.stt.base import Transcript


class FakeTranscriber:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe(self, audio, **kw):
        self.calls += 1
        return Transcript(text=self.text, backend="fake", model="fake", language="en")


class FakeInjector:
    def __init__(self, ok=True):
        self.typed = []
        self.ok = ok
        self.method = "auto"

    def inject(self, text):
        self.typed.append(text)
        return self.ok


@pytest.fixture
def rig(tmp_path):
    cfg = Config(tmp_path / "config.json")
    cfg.load()
    cfg.set("custom_words", [
        {"word": "ultrahuman", "replacement": "Ultrahuman", "matching_threshold": 0.85},
    ], save=False)
    store = Store(tmp_path / "t.db")
    ctl = DictationController(cfg, store, Events())
    return cfg, store, ctl


def run(ctl, transcript_text, injector=None):
    ctl.transcriber = FakeTranscriber(transcript_text)
    ctl.injector = injector or FakeInjector()
    ctl._process(np.zeros(16_000, dtype=np.float32), 1000)
    return ctl.injector


def test_full_chain_cleans_and_types(rig):
    cfg, store, ctl = rig
    inj = run(ctl, "um so the ultra human revenue was uh twelve crore full stop")
    assert inj.typed == ["So the Ultrahuman revenue was twelve crore."]


def test_history_row_is_written_with_raw_and_clean(rig):
    cfg, store, ctl = rig
    run(ctl, "um hello there")
    row = store.last_dictation()
    assert row["raw_text"] == "um hello there"
    assert row["text"] == "Hello there"
    assert row["backend"] == "fake"
    assert row["word_count"] == 2


def test_scratch_that_is_honoured(rig):
    cfg, store, ctl = rig
    inj = run(ctl, "ship on friday full stop scratch that ship on monday")
    assert inj.typed == ["Ship on monday"]


def test_empty_transcript_types_nothing(rig):
    cfg, store, ctl = rig
    inj = run(ctl, "   ")
    assert inj.typed == []
    assert store.last_dictation() is None


def test_injection_failure_is_reported_not_swallowed(rig):
    cfg, store, ctl = rig
    seen = []
    ctl.events = Events(on_error=seen.append)
    run(ctl, "hello", injector=FakeInjector(ok=False))
    assert seen and "administrator" in seen[0]
    # the dictation is still saved so the text is recoverable from history
    assert store.last_dictation()["text"] == "Hello"


def test_transcriber_error_surfaces_and_sets_error_state(rig):
    cfg, store, ctl = rig
    seen = []
    ctl.events = Events(on_error=seen.append)

    class Boom:
        def transcribe(self, audio, **kw):
            raise RuntimeError("model exploded")

    ctl.transcriber = Boom()
    ctl.injector = FakeInjector()
    ctl._process(np.zeros(16_000, dtype=np.float32), 1000)
    assert seen == ["model exploded"]
    assert ctl.state == "error"
    assert ctl.injector.typed == []


def test_dictionary_reloads_when_config_changes(rig):
    cfg, store, ctl = rig
    assert len(ctl.dictionary) == 1
    cfg.set("custom_words", [
        {"word": "zoho", "replacement": "Zoho", "matching_threshold": 0.9},
        {"word": "ind as", "replacement": "Ind AS", "matching_threshold": 0.9},
    ])
    assert len(ctl.dictionary) == 2
    inj = run(ctl, "posted in zoho under ind as one one five")
    assert "Zoho" in inj.typed[0] and "Ind AS" in inj.typed[0]


def test_post_processor_off_by_default_makes_no_network_call(rig, monkeypatch):
    cfg, store, ctl = rig
    called = []
    monkeypatch.setattr("muesli_win.controller.chat",
                        lambda *a, **k: called.append(1) or "x")
    run(ctl, "hello there")
    assert not called


def test_post_processor_failure_falls_back_to_deterministic_text(rig, monkeypatch):
    cfg, store, ctl = rig
    cfg.set("enable_post_processor", True)
    from muesli_win.text.llm import LLMError
    monkeypatch.setattr("muesli_win.controller.chat",
                        lambda *a, **k: (_ for _ in ()).throw(LLMError("ollama down")))
    notices = []
    ctl.events = Events(on_notice=notices.append)
    inj = run(ctl, "um hello there")
    assert inj.typed == ["Hello there"]
    assert notices and "ollama down" in notices[0]


def test_post_processor_junk_output_is_rejected(rig, monkeypatch):
    cfg, store, ctl = rig
    cfg.set("enable_post_processor", True)
    monkeypatch.setattr("muesli_win.controller.chat",
                        lambda *a, **k: "Sure! Here is the cleaned text:\n```\n" + "z" * 400 + "\n```")
    inj = run(ctl, "hello there friend")
    assert inj.typed == ["Hello there friend"]      # junk discarded


# --- Quill ------------------------------------------------------------------
def test_quill_rewrites_the_selection(rig, monkeypatch):
    cfg, store, ctl = rig
    cfg.set("enable_quill", True)
    ctl.transcriber = FakeTranscriber("make this more formal")
    ctl.injector = FakeInjector()
    ctl._quill_selection = "hey can you send that over"
    seen = {}

    def fake_chat(target, system, user, **kw):
        seen["user"] = user
        return "Please could you send that across."

    monkeypatch.setattr("muesli_win.controller.chat", fake_chat)
    ctl._process_quill(np.zeros(16_000, dtype=np.float32))
    assert ctl.injector.typed == ["Please could you send that across."]
    assert "make this more formal" in seen["user"].lower()
    assert "hey can you send that over" in seen["user"]
    assert ctl._quill_selection == ""          # cleared after use


def test_quill_with_no_selection_generates_new_text(rig, monkeypatch):
    cfg, store, ctl = rig
    cfg.set("enable_quill", True)
    ctl.transcriber = FakeTranscriber("write a one line apology")
    ctl.injector = FakeInjector()
    ctl._quill_selection = ""
    monkeypatch.setattr("muesli_win.controller.chat",
                        lambda *a, **k: "Apologies for the delay.")
    ctl._process_quill(np.zeros(16_000, dtype=np.float32))
    assert ctl.injector.typed == ["Apologies for the delay."]


def test_quill_llm_failure_types_nothing_and_reports(rig, monkeypatch):
    cfg, store, ctl = rig
    from muesli_win.text.llm import LLMError
    ctl.transcriber = FakeTranscriber("tidy this")
    ctl.injector = FakeInjector()
    ctl._quill_selection = "some text"
    errs = []
    ctl.events = Events(on_error=errs.append)
    monkeypatch.setattr("muesli_win.controller.chat",
                        lambda *a, **k: (_ for _ in ()).throw(LLMError("no model")))
    ctl._process_quill(np.zeros(16_000, dtype=np.float32))
    assert ctl.injector.typed == []
    assert errs and "Quill" in errs[0]


def test_quill_output_may_be_longer_than_the_input(rig, monkeypatch):
    """Dictation cleanup rejects growth; Quill must allow it."""
    cfg, store, ctl = rig
    ctl.transcriber = FakeTranscriber("expand this into a paragraph")
    ctl.injector = FakeInjector()
    ctl._quill_selection = "ship monday"
    long = ("We are targeting a Monday shipment. " * 8).strip()
    monkeypatch.setattr("muesli_win.controller.chat", lambda *a, **k: long)
    ctl._process_quill(np.zeros(16_000, dtype=np.float32))
    assert ctl.injector.typed == [long]
