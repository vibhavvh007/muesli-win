"""The floating indicator.

A small frameless always-on-top pill that shows what dictation is doing and how
loud you are. It must never take focus - if it did, the text would be typed into
the indicator instead of the document the user was in - hence
WindowDoesNotAcceptFocus plus the Tool window type.

Position is remembered per machine in config (Muesli keeps its coordinates out
of the shared settings file for the same reason: monitors differ).
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

W, H = 168, 44
LABELS = {"idle": "", "listening": "Listening", "thinking": "Transcribing",
          "error": "Error", "meeting": "Recording"}


class FloatingIndicator(QWidget):
    moved = Signal(int, int)

    def __init__(self, cfg) -> None:
        super().__init__(None)
        self.cfg = cfg
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(W, H)

        self._state = "idle"
        self._level = 0.0
        self._decay = QTimer(self)
        self._decay.timeout.connect(self._tick)
        self._decay.setInterval(60)
        self._drag_from: QPoint | None = None
        self._hint = ""
        self._restore_position()

    # -- placement ---------------------------------------------------------
    def _restore_position(self) -> None:
        pos = self.cfg.get("indicator_position")
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        if isinstance(pos, (list, tuple)) and len(pos) == 2 and _on_screen(pos):
            self.move(int(pos[0]), int(pos[1]))
        elif geo is not None:
            self.move(geo.center().x() - W // 2, geo.bottom() - H - 90)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_from)
            e.accept()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_from is not None:
            self._drag_from = None
            p = self.pos()
            self.cfg.set("indicator_position", [p.x(), p.y()])
            self.moved.emit(p.x(), p.y())

    # -- state -------------------------------------------------------------
    def set_state(self, state: str, hint: str = "") -> None:
        self._state = state
        self._hint = hint

        if state == "idle" or not self.cfg.get("show_floating_indicator", True):
            # Also hides when the setting is switched off while it is on screen,
            # which previously left it stuck until the next idle transition.
            self._decay.stop()
            self._level = 0.0
            self.hide()
            return

        self.show()
        self.raise_()
        # The level meter only animates while there is audio to show. Running it
        # during transcription just burns wakeups behind a static pill.
        if state in ("listening", "meeting"):
            if not self._decay.isActive():
                self._decay.start()
        else:
            self._decay.stop()
            self._level = 0.0
        self.update()

    def set_level(self, level: float) -> None:
        self._level = max(self._level * 0.6, min(1.0, float(level)))

    def _tick(self) -> None:
        self._level *= 0.82
        self.update()

    # -- paint -------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor("#" + str(self.cfg.get("recording_color_hex", "1e1e2e")).lstrip("#"))
        bg.setAlpha(238)

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, W, H), H / 2, H / 2)
        p.fillPath(path, bg)

        accent = QColor({"listening": "#e0544d", "thinking": "#e0a23c",
                         "error": "#ff6b6b", "meeting": "#4d8ae0"}.get(self._state, "#9aa"))

        # level bars
        bars, bx, bw, gap = 5, 16, 4, 5
        for i in range(bars):
            phase = 0.55 + 0.45 * ((i % 3) / 2.0)
            amp = self._level * phase
            h = 6 + amp * (H - 22)
            r = QRectF(bx + i * (bw + gap), (H - h) / 2, bw, h)
            p.fillPath(_rounded(r, bw / 2), accent)

        label = LABELS.get(self._state, "")
        if self._hint:
            label = self._hint
        elif self.cfg.get("show_hotkey_on_floating_indicator", False):
            hk = self.cfg.get("dictation_hotkey", {}).get("label", "")
            label = f"{label}  ·  {hk}" if hk else label
        if label:
            p.setPen(QColor("#f2f2f7"))
            f = QFont()
            f.setPointSizeF(9.5)
            p.setFont(f)
            p.drawText(QRectF(66, 0, W - 76, H), Qt.AlignVCenter | Qt.AlignLeft, label)
        p.end()


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _on_screen(pos) -> bool:
    try:
        x, y = int(pos[0]), int(pos[1])
    except (TypeError, ValueError, IndexError):
        return False
    for screen in QGuiApplication.screens():
        g = screen.availableGeometry()
        if g.contains(QPoint(x + W // 2, y + H // 2)):
            return True
    return False
