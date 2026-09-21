"""Application bootstrap: builds everything, wires the signals, runs the loop.

Controller events arrive on worker threads, so every one of them is marshalled
onto the Qt thread through a signal before it touches a widget. Doing that
anywhere else is the classic way to make a tray app crash after twenty minutes.
"""
from __future__ import annotations

import logging
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import autostart, logging_setup, paths
from .config import Config
from .controller import DictationController, Events
from .meetings import hooks as meeting_hooks
from .meetings import summarize
from .meetings.recorder import MeetingRecorder
from .single_instance import SingleInstance
from .storage.db import Store
from .stt.registry import LazyTranscriber
from .ui import icons
from .ui.dashboard import Dashboard
from .ui.indicator import FloatingIndicator
from .ui.settings import SettingsWindow
from .ui.tray import Tray

log = logging.getLogger(__name__)


class Bridge(QObject):
    """Thread hop: worker thread -> Qt thread."""
    state = Signal(str)
    level = Signal(float)
    text = Signal(str)
    error = Signal(str)
    notice = Signal(str)
    meeting_segment = Signal(str, str)
    meeting_done = Signal(str)


class MuesliApp:
    def __init__(self, argv: list[str]) -> None:
        self.cfg = Config().load()
        logging_setup.setup(self.cfg.get("win_log_level", "INFO"))
        paths.ensure_dirs()
        log.info("Muesli for Windows starting (frozen=%s)", paths.is_frozen())

        # Reuse an existing instance rather than creating a second one: Qt allows
        # only one per process, and embedding (tests, a host app) is otherwise
        # impossible.
        self.qt = QApplication.instance() or QApplication(argv)
        self.qt.setApplicationName("Muesli")
        self.qt.setOrganizationName("Muesli")
        self.qt.setWindowIcon(icons.app_icon())
        self.qt.setQuitOnLastWindowClosed(False)      # tray app

        self.store = Store()
        self.bridge = Bridge()
        self.controller = DictationController(self.cfg, self.store, Events(
            on_state=self.bridge.state.emit,
            on_level=self.bridge.level.emit,
            on_text=self.bridge.text.emit,
            on_error=self.bridge.error.emit,
            on_notice=self.bridge.notice.emit,
        ))

        self.meeting_stt = LazyTranscriber(self.cfg, purpose="meeting")
        self.recorder: MeetingRecorder | None = None

        self.tray = Tray(self.cfg)
        self.indicator = FloatingIndicator(self.cfg)
        self.dashboard = Dashboard(self.cfg, self.store)
        self.settings: SettingsWindow | None = None

        self._wire()
        autostart.sync(self.cfg)

    # -- wiring ------------------------------------------------------------
    def _wire(self) -> None:
        b = self.bridge
        b.state.connect(self._on_state, Qt.QueuedConnection)
        b.level.connect(self.indicator.set_level, Qt.QueuedConnection)
        b.error.connect(self._on_error, Qt.QueuedConnection)
        b.notice.connect(self._on_notice, Qt.QueuedConnection)
        b.text.connect(lambda _t: self.dashboard.refresh(), Qt.QueuedConnection)
        b.meeting_segment.connect(self._on_meeting_segment, Qt.QueuedConnection)
        b.meeting_done.connect(self._on_meeting_done, Qt.QueuedConnection)

        self.tray.toggle_dictation.connect(self.controller.toggle)
        self.tray.toggle_meeting.connect(self.toggle_meeting)
        self.tray.open_dashboard.connect(self._show_dashboard)
        self.tray.open_settings.connect(self._show_settings)
        self.tray.rearm_hotkey.connect(self._rearm)
        self.tray.quit_app.connect(self.quit)

        self.dashboard.summarise_requested.connect(self._summarise_meeting)
        self.cfg.subscribe(self._on_config_change)

    def _on_config_change(self, key: str, _value) -> None:
        if key == "launch_at_login":
            autostart.sync(self.cfg)
        elif key in ("meeting_transcription_backend", "meeting_transcription_model"):
            self.meeting_stt.reload()
        elif key == "win_log_level":
            logging_setup.setup(self.cfg.get("win_log_level", "INFO"))

    # -- ui slots ----------------------------------------------------------
    def _on_state(self, state: str) -> None:
        meeting_active = self.recorder is not None and self.recorder.running
        self.tray.set_state("meeting" if (meeting_active and state == "idle") else state)
        if meeting_active and state == "idle":
            self.indicator.set_state("meeting")
        else:
            self.indicator.set_state(state)

    def _on_error(self, message: str) -> None:
        log.error("ui error: %s", message)
        self.tray.notify("Muesli", message, QSystemTrayIcon.Warning)

    def _on_notice(self, message: str) -> None:
        self.tray.notify("Muesli", message, QSystemTrayIcon.Information)

    def _show_dashboard(self) -> None:
        self.dashboard.refresh()
        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    def _show_settings(self) -> None:
        if self.settings is None:
            self.settings = SettingsWindow(self.cfg)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def _rearm(self) -> None:
        ok = self.controller.hook.reinstall()
        self.tray.notify("Muesli", "Hotkey re-armed." if ok else
                         "Could not reinstall the hotkey - see the log.",
                         QSystemTrayIcon.Information if ok else QSystemTrayIcon.Warning)

    # -- meetings ----------------------------------------------------------
    def toggle_meeting(self) -> None:
        if self.recorder is not None and self.recorder.running:
            self.tray.notify("Muesli", "Finishing the recording…")
            threading.Thread(target=self._stop_meeting, daemon=True).start()
            return
        self.meeting_stt.preload()
        self.recorder = MeetingRecorder(
            self.cfg, self.store, self.meeting_stt,
            on_segment=self.bridge.meeting_segment.emit,
            on_error=self.bridge.error.emit,
            on_notice=self.bridge.notice.emit,
        )
        mid = self.recorder.start()
        if mid:
            self.tray.set_meeting_active(True)
            self.tray.set_state("meeting")
            self.indicator.set_state("meeting")
            self.tray.notify("Muesli", "Recording the meeting. Audio is not kept."
                             if self.cfg.get("meeting_recording_save_policy") == "never"
                             else "Recording the meeting.")
        else:
            self.recorder = None

    def _stop_meeting(self) -> None:
        rec, self.recorder = self.recorder, None
        mid = rec.stop() if rec else None
        self.bridge.meeting_done.emit(mid or "")

    def _on_meeting_segment(self, speaker: str, text: str) -> None:
        # An empty segment is the "meeting finished" marker; showing the pill
        # again at that point is exactly the flicker we are trying to remove.
        if not speaker and not text:
            return
        if self.recorder is not None and self.recorder.running:
            self.indicator.set_state("meeting", hint=f"{speaker}: {text[:24]}")

    def _on_meeting_done(self, mid: str) -> None:
        self.tray.set_meeting_active(False)
        self.tray.set_state("idle")
        self.indicator.set_state("idle")
        self.dashboard.refresh()
        if not mid:
            return
        self.tray.notify("Muesli", "Meeting saved. Generating notes…")
        self._summarise_meeting(mid)

    def _summarise_meeting(self, mid: str) -> None:
        threading.Thread(target=self._summarise_worker, args=(mid,), daemon=True).start()

    def _summarise_worker(self, mid: str) -> None:
        meeting = self.store.get_meeting(mid)
        if not meeting:
            return
        transcript = meeting.get("transcript") or ""
        if not transcript.strip():
            self.bridge.notice.emit("Nothing was transcribed, so there are no notes.")
            return
        try:
            notes, template_id = summarize.summarise(
                self.cfg, transcript, self.cfg.get("default_meeting_template_id", "auto"))
            model = self.cfg.get("meeting_summary_model", "")
            self.store.set_meeting_notes(mid, notes, model, template_id)
            if not meeting.get("title"):
                title = summarize.make_title(self.cfg, transcript)
                if title:
                    self.store.set_meeting_title(mid, title)
            self.bridge.notice.emit("Meeting notes are ready.")
        except Exception as exc:
            self.bridge.error.emit(
                f"Notes could not be generated: {exc}. The transcript is saved.")
        finally:
            meeting = self.store.get_meeting(mid) or meeting
            segments = self.store.segments(mid)
            if self.cfg.get("auto_export_markdown_enabled", False):
                try:
                    from .meetings import export as export_mod
                    export_mod.write_markdown(
                        meeting, segments,
                        self.cfg.get("auto_export_markdown_content", "notes"))
                except Exception:
                    log.exception("auto-export failed")
            try:
                meeting_hooks.run(self.cfg, meeting, segments)
            except Exception:
                log.exception("meeting hook dispatch failed")
            self.bridge.state.emit("idle")

    # -- lifecycle ---------------------------------------------------------
    def run(self, show_dashboard: bool | None = None) -> int:
        if not self.controller.start():
            QMessageBox.warning(
                None, "Muesli",
                "The global hotkey could not be installed, so dictation will not "
                "respond to the keyboard. You can still start dictation from the "
                "tray icon.\n\nThis usually means another app has taken the hook, "
                "or Muesli needs to be restarted.")
        if self.cfg.get("enable_meeting_recording_hotkey", False):
            self.controller.hook.register_combo(0x52, {"ctrl", "shift"}, self.toggle_meeting)
        if self.cfg.get("enable_quill", False):
            quill_vk = int(self.cfg.get("quill_hotkey", {}).get("vk", 0xA3))
            if quill_vk == self.controller._hotkey_vk():
                log.warning("quill hotkey clashes with dictation; quill disabled")
                self.tray.notify("Muesli", "Quill uses the same key as dictation, so it "
                                           "has been left off. Change it in Settings.",
                                 QSystemTrayIcon.Warning)
            else:
                self.controller.hook.register_combo(quill_vk, set(),
                                                    self.controller.toggle_quill)

        want_dash = (self.cfg.get("open_dashboard_on_launch", True)
                     if show_dashboard is None else show_dashboard)
        if want_dash:
            QTimer.singleShot(400, self._show_dashboard)

        QTimer.singleShot(1500, lambda: self.tray.notify(
            "Muesli is running",
            f"Hold {self.cfg.get('dictation_hotkey', {}).get('label', 'Right Alt')} to "
            f"dictate. Double-tap for hands-free."))
        return self.qt.exec()

    def quit(self) -> None:
        log.info("shutting down")
        try:
            if self.recorder is not None and self.recorder.running:
                self.recorder.stop()
            self.controller.stop()
            self.tray.hide()
            self.store.close()
        except Exception:
            log.exception("error during shutdown")
        self.qt.quit()


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    guard = SingleInstance()
    if not guard.acquire():
        _ = QApplication.instance() or QApplication(argv)
        QMessageBox.information(None, "Muesli", "Muesli is already running - look for "
                                                "the pencil icon in the system tray.")
        return 0
    try:
        show_dash = None
        if "--tray" in argv:
            show_dash = False
        return MuesliApp(argv).run(show_dashboard=show_dash)
    finally:
        guard.release()
