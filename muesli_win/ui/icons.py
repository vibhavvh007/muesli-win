"""Icons drawn at runtime.

Nothing to ship, nothing to go missing from the bundle, and the tray icon can
change colour with state (idle / listening / thinking / error) without carrying
four .ico files around.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

STATE_COLOURS = {
    "idle": "#8a8a99",
    "listening": "#e0544d",
    "thinking": "#e0a23c",
    "error": "#b23b3b",
    "meeting": "#4d8ae0",
}


def _pen_glyph(p: QPainter, rect: QRectF, colour: QColor) -> None:
    """A pencil stroke - matches Muesli's `pencil.line` menu-bar icon."""
    pen = QPen(colour)
    pen.setWidthF(rect.width() * 0.13)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(rect.left() + rect.width() * 0.22, rect.bottom() - rect.height() * 0.22)
    path.lineTo(rect.right() - rect.width() * 0.22, rect.top() + rect.height() * 0.22)
    p.drawPath(path)
    p.drawLine(rect.left() + rect.width() * 0.18, rect.bottom() - rect.height() * 0.16,
               rect.left() + rect.width() * 0.34, rect.bottom() - rect.height() * 0.30)


def tray_icon(state: str = "idle", size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    colour = QColor(STATE_COLOURS.get(state, STATE_COLOURS["idle"]))
    _pen_glyph(p, QRectF(0, 0, size, size), colour)
    if state in ("listening", "meeting"):
        r = size * 0.26
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(colour))
        p.drawEllipse(QRectF(size - r - 1, size - r - 1, r, r))
    p.end()
    return QIcon(pm)


def app_icon(size: int = 256) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#1e1e2e")))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    _pen_glyph(p, QRectF(size * 0.16, size * 0.16, size * 0.68, size * 0.68),
               QColor("#f2f2f7"))
    p.end()
    return QIcon(pm)


def write_ico(path: str, size: int = 256) -> str:
    """Emit a .ico for the installer and the exe."""
    app_icon(size).pixmap(size, size).save(path, "ICO")
    return path
