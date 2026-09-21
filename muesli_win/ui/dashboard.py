"""Dashboard: dictation history, meetings, notes, export."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..meetings import export as export_mod
from . import icons

log = logging.getLogger(__name__)


def _when(ts: float) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ts or 0))


def _fit_to_screen(width: int, height: int) -> tuple[int, int]:
    """Never open larger than the screen actually available.

    A hard-coded size is wrong on a 1366x768 laptop at 150% scaling, where the
    usable area is around 910x510 logical pixels.
    """
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return width, height
    avail = screen.availableGeometry()
    return (min(width, max(480, avail.width() - 80)),
            min(height, max(360, avail.height() - 80)))


class Dashboard(QMainWindow):
    summarise_requested = Signal(str)

    def __init__(self, cfg, store, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.store = store
        self.setWindowTitle("Muesli")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(560, 400)
        self.resize(*_fit_to_screen(940, 640))

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_dictations(), "Dictations")
        self.tabs.addTab(self._tab_meetings(), "Meetings")
        self.setCentralWidget(self.tabs)

        self.status = QLabel("")
        self.statusBar().addPermanentWidget(self.status)
        self.refresh()

    # -- dictations --------------------------------------------------------
    def _tab_dictations(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.dict_table = QTableWidget(0, 4)
        self.dict_table.setHorizontalHeaderLabels(["When", "Text", "Where", "Words"])
        self.dict_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.dict_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.dict_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dict_table.itemSelectionChanged.connect(self._show_dictation)
        v.addWidget(self.dict_table, 3)

        self.dict_detail = QPlainTextEdit()
        self.dict_detail.setReadOnly(True)
        v.addWidget(self.dict_detail, 1)

        row = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(self._copy_dictation)
        delete = QPushButton("Delete")
        delete.clicked.connect(self._delete_dictation)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(copy)
        row.addWidget(delete)
        row.addStretch(1)
        row.addWidget(refresh)
        v.addLayout(row)
        return w

    def _selected_dictation(self) -> dict | None:
        rows = {i.row() for i in self.dict_table.selectedIndexes()}
        if not rows:
            return None
        item = self.dict_table.item(min(rows), 0)
        return self.store.get_dictation(item.data(Qt.UserRole)) if item else None

    def _show_dictation(self) -> None:
        d = self._selected_dictation()
        if not d:
            return
        note = " (post-processed)" if d.get("post_processed") else ""
        self.dict_detail.setPlainText(
            f"{d['text']}\n\n--- raw{note} ---\n{d['raw_text']}\n\n"
            f"{d['backend']} / {d['model']}  ·  {d['latency_ms']} ms"
        )

    def _copy_dictation(self) -> None:
        d = self._selected_dictation()
        if d:
            QGuiApplication.clipboard().setText(d["text"])
            self.status.setText("Copied")

    def _delete_dictation(self) -> None:
        d = self._selected_dictation()
        if not d:
            return
        self.store.delete_dictation(d["id"])
        self.refresh()

    # -- meetings ----------------------------------------------------------
    def _tab_meetings(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        split = QSplitter(Qt.Horizontal)

        self.meet_list = QListWidget()
        self.meet_list.currentItemChanged.connect(self._show_meeting)
        split.addWidget(self.meet_list)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.meet_detail = QPlainTextEdit()
        self.meet_detail.setReadOnly(True)
        rv.addWidget(self.meet_detail)
        split.addWidget(right)
        split.setSizes([260, 660])
        outer.addWidget(split)

        row = QHBoxLayout()
        gen = QPushButton("Generate notes")
        gen.clicked.connect(self._generate_notes)
        md = QPushButton("Export Markdown")
        md.clicked.connect(lambda: self._export("markdown"))
        pdf = QPushButton("Export PDF")
        pdf.clicked.connect(lambda: self._export("pdf"))
        dele = QPushButton("Delete")
        dele.clicked.connect(self._delete_meeting)
        row.addWidget(gen)
        row.addWidget(md)
        row.addWidget(pdf)
        row.addStretch(1)
        row.addWidget(dele)
        outer.addLayout(row)
        return w

    def _selected_meeting(self) -> dict | None:
        item = self.meet_list.currentItem()
        return self.store.get_meeting(item.data(Qt.UserRole)) if item else None

    def _show_meeting(self) -> None:
        m = self._selected_meeting()
        if not m:
            return
        segs = self.store.segments(m["id"])
        body = m.get("notes") or "_No notes generated yet._"
        self.meet_detail.setPlainText(
            export_mod.to_markdown({**m, "notes": body}, segs, "both"))

    def _generate_notes(self) -> None:
        m = self._selected_meeting()
        if not m:
            return
        self.status.setText("Generating notes…")
        self.summarise_requested.emit(m["id"])

    def _export(self, fmt: str) -> None:
        m = self._selected_meeting()
        if not m:
            return
        segs = self.store.segments(m["id"])
        content = self.cfg.get("auto_export_markdown_content", "notes")
        try:
            if fmt == "pdf":
                path = export_mod.write_pdf(m, segs, content)
            else:
                path = export_mod.write_markdown(m, segs, content)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.status.setText(f"Saved {path.name}")
        QMessageBox.information(self, "Exported", f"Saved to\n{path}")

    def _delete_meeting(self) -> None:
        m = self._selected_meeting()
        if not m:
            return
        if QMessageBox.question(self, "Delete meeting",
                                f"Delete “{m.get('title') or 'this meeting'}” "
                                "and its transcript?") != QMessageBox.Yes:
            return
        self.store.delete_meeting(m["id"])
        self.refresh()

    # -- shared ------------------------------------------------------------
    def refresh(self) -> None:
        rows = self.store.list_dictations(limit=300)
        self.dict_table.setRowCount(0)
        for d in rows:
            r = self.dict_table.rowCount()
            self.dict_table.insertRow(r)
            first = QTableWidgetItem(_when(d["created_at"]))
            first.setData(Qt.UserRole, d["id"])
            self.dict_table.setItem(r, 0, first)
            preview = d["text"][:160].replace("\n", " ")
            self.dict_table.setItem(r, 1, QTableWidgetItem(preview))
            self.dict_table.setItem(r, 2, QTableWidgetItem(d.get("app_name", "")))
            self.dict_table.setItem(r, 3, QTableWidgetItem(str(d.get("word_count", 0))))

        self.meet_list.clear()
        for m in self.store.list_meetings(limit=200):
            label = m.get("title") or f"Meeting {_when(m['created_at'])}"
            mins = int((m.get("duration_ms") or 0) / 60000)
            item = QListWidgetItem(f"{label}\n{_when(m['created_at'])} · {mins} min")
            item.setData(Qt.UserRole, m["id"])
            self.meet_list.addItem(item)

        t = self.store.totals()
        self.status.setText(
            f"{t['dictations']} dictations · {t['words']:,} words · "
            f"{t['seconds'] / 3600:.1f} h spoken"
        )

    def closeEvent(self, event) -> None:
        event.ignore()          # tray app: closing the window must not quit
        self.hide()
