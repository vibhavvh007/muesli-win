"""Boots the whole application offscreen.

This is the test that would have caught a bad signal connection, a widget built
on the wrong thread, or a typo in a slot name - the failures that only show up
when the app actually starts.
"""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def app(tmp_path, monkeypatch):
    from muesli_win import paths
    monkeypatch.setattr(paths, "app_support_dir", lambda: tmp_path / "support")
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(paths, "config_path", lambda: tmp_path / "support" / "config.json")
    monkeypatch.setattr(paths, "db_path", lambda: tmp_path / "support" / "muesli.db")
    monkeypatch.setattr(paths, "models_dir", lambda: tmp_path / "cache" / "models")
    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path / "support" / "logs")
    monkeypatch.setattr(paths, "recordings_dir", lambda: tmp_path / "cache" / "rec")

    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        QApplication([])

    from muesli_win.app import MuesliApp
    a = MuesliApp([])
    yield a
    a.controller.stop()
    a.store.close()


def test_app_builds_all_surfaces(app):
    assert app.tray is not None
    assert app.indicator is not None
    assert app.dashboard is not None
    assert app.controller is not None


def test_state_events_reach_tray_and_indicator(app):
    app.bridge.state.emit("listening")
    app.qt.processEvents()
    assert app.indicator.isVisible()
    assert "listening" in app.tray.icon.toolTip().lower()

    app.bridge.state.emit("idle")
    app.qt.processEvents()
    assert not app.indicator.isVisible()


def test_error_event_does_not_raise(app):
    app.bridge.error.emit("something went wrong")
    app.qt.processEvents()


def test_settings_window_opens_and_writes_config(app):
    app._show_settings()
    app.qt.processEvents()
    s = app.settings
    assert s is not None
    s._set("hotkey_trigger_threshold_ms", 300)
    assert app.cfg.get("hotkey_trigger_threshold_ms") == 300
    # and it survives a reload from disk
    from muesli_win.config import Config
    assert Config(app.cfg.path).load().get("hotkey_trigger_threshold_ms") == 300


def test_dashboard_refreshes_after_a_dictation(app):
    app.store.add_dictation(text="hello world", raw_text="hello world", backend="t")
    app.bridge.text.emit("hello world")
    app.qt.processEvents()
    assert app.dashboard.dict_table.rowCount() == 1


def test_meeting_summary_without_llm_reports_not_crashes(app, monkeypatch):
    from muesli_win.text.llm import LLMError
    mid = app.store.create_meeting("t")
    app.store.add_segment(mid, 0, 1000, "hello", "you")
    app.store.finish_meeting(mid, transcript="[00:00] You: hello", duration_ms=1000)
    monkeypatch.setattr("muesli_win.meetings.summarize.chat",
                        lambda *a, **k: (_ for _ in ()).throw(LLMError("no key")))
    seen = []
    app.bridge.error.connect(seen.append)
    app._summarise_worker(mid)
    app.qt.processEvents()
    assert seen and "no key" in seen[0]
    assert app.store.get_meeting(mid)["transcript"]     # transcript survives


def test_closing_dashboard_does_not_quit(app):
    from PySide6.QtGui import QCloseEvent
    ev = QCloseEvent()
    app.dashboard.closeEvent(ev)
    assert not ev.isAccepted()
