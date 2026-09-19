"""System tray icon and menu - the app's only persistent surface."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import icons

log = logging.getLogger(__name__)


class Tray(QObject):
    toggle_dictation = Signal()
    toggle_meeting = Signal()
    open_dashboard = Signal()
    open_settings = Signal()
    rearm_hotkey = Signal()
    quit_app = Signal()

    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.icon = QSystemTrayIcon(icons.tray_icon("idle"), parent)
        self._state = "idle"
        self._build_menu()
        self.icon.activated.connect(self._on_activated)
        self.icon.show()
        self.set_state("idle")

    def _build_menu(self) -> None:
        m = QMenu()
        self.act_dictate = QAction("Start dictation", m)
        self.act_dictate.triggered.connect(self.toggle_dictation.emit)
        m.addAction(self.act_dictate)

        self.act_meeting = QAction("Start meeting recording", m)
        self.act_meeting.triggered.connect(self.toggle_meeting.emit)
        m.addAction(self.act_meeting)
        m.addSeparator()

        act_dash = QAction("Dashboard…", m)
        act_dash.triggered.connect(self.open_dashboard.emit)
        m.addAction(act_dash)

        act_set = QAction("Settings…", m)
        act_set.triggered.connect(self.open_settings.emit)
        m.addAction(act_set)
        m.addSeparator()

        act_rearm = QAction("Re-arm hotkey", m)
        act_rearm.setToolTip("Reinstall the global keyboard hook if the hotkey "
                             "has stopped responding")
        act_rearm.triggered.connect(self.rearm_hotkey.emit)
        m.addAction(act_rearm)
        m.addSeparator()

        act_quit = QAction("Quit Muesli", m)
        act_quit.triggered.connect(self.quit_app.emit)
        m.addAction(act_quit)

        self.menu = m
        self.icon.setContextMenu(m)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.open_dashboard.emit()
        elif reason == QSystemTrayIcon.MiddleClick:
            self.toggle_dictation.emit()

    # -- state -------------------------------------------------------------
    def set_state(self, state: str) -> None:
        self._state = state
        self.icon.setIcon(icons.tray_icon(state))
        hk = self.cfg.get("dictation_hotkey", {}).get("label", "Right Alt")
        base = {"idle": "Muesli - ready", "listening": "Muesli - listening",
                "thinking": "Muesli - transcribing", "error": "Muesli - error",
                "meeting": "Muesli - recording meeting"}.get(state, "Muesli")
        if self.cfg.get("show_hotkey_in_menu_bar", True) and state == "idle":
            base = f"{base}  ({hk})"
        self.icon.setToolTip(base)
        self.act_dictate.setText("Stop dictation" if state in ("listening", "thinking")
                                 else "Start dictation")

    def set_meeting_active(self, active: bool) -> None:
        self.act_meeting.setText("Stop meeting recording" if active
                                 else "Start meeting recording")

    def notify(self, title: str, message: str,
               level: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.Information) -> None:
        try:
            self.icon.showMessage(title, message, level, 6000)
        except Exception:
            log.info("%s: %s", title, message)

    def hide(self) -> None:
        self.icon.hide()
