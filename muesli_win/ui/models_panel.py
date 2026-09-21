"""Model manager: see what is downloaded, and download it deliberately.

Before this existed the only way to fetch a model was to press the dictation
hotkey and wait, which is indistinguishable from the app hanging. Here the user
can see the size, start the download, watch it progress and know when it is
usable.

Progress comes from watching the cache folder grow rather than from a download
callback - see stt/download.bytes_on_disk for why.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..stt import download as dl

log = logging.getLogger(__name__)


class _BenchWorker(QObject):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, model: str, device: str, compute: str) -> None:
        super().__init__()
        self.model, self.device, self.compute = model, device, compute

    def run(self) -> None:
        try:
            from ..bench import benchmark, interpret
            r = benchmark(self.model, device=self.device, compute=self.compute)
            payload = {"result": r, "interpretation": interpret(r)}
            self.done.emit(payload)
        except Exception as exc:
            self.failed.emit(str(exc))


class _Worker(QObject):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, name: str, device: str, compute: str) -> None:
        super().__init__()
        self.name, self.device, self.compute = name, device, compute

    def run(self) -> None:
        try:
            self.done.emit(dl.download(self.name, device=self.device,
                                       compute=self.compute))
        except Exception as exc:
            self.failed.emit(str(exc))


class ModelsPanel(QWidget):
    model_activated = Signal(str, str)      # backend, model

    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._downloading: str = ""
        self._baseline = 0
        self._benchmarking = False
        self._bench_thread: QThread | None = None
        self._bench_worker: _BenchWorker | None = None

        v = QVBoxLayout(self)
        blurb = QLabel(
            "Models run on this PC and download once. Pick one and press "
            "Download before you first dictate, so the first press is instant.")
        blurb.setWordWrap(True)
        blurb.setMinimumWidth(1)
        v.addWidget(blurb)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Model", "Engine", "Size", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        # Bounded height: the table is inside a scrolling tab, so it must not
        # demand room for all 14 rows and push the dialog past the screen edge.
        self.table.setMinimumWidth(1)
        self.table.setMinimumHeight(140)
        self.table.setMaximumHeight(260)
        v.addWidget(self.table)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(1)
        v.addWidget(self.status)

        row = QHBoxLayout()
        self.btn_download = QPushButton("Download")
        self.btn_download.clicked.connect(self._download)
        self.btn_use = QPushButton("Use for dictation")
        self.btn_use.clicked.connect(self._use)
        self.btn_test = QPushButton("Test on this PC")
        self.btn_test.setToolTip(
            "Measure how fast the selected model actually runs here, and what "
            "that means for dictation and meetings")
        self.btn_test.clicked.connect(self._benchmark)
        self.btn_folder = QPushButton("Open folder")
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        row.addWidget(self.btn_download)
        row.addWidget(self.btn_use)
        row.addWidget(self.btn_test)
        row.addStretch(1)
        row.addWidget(self.btn_folder)
        row.addWidget(self.btn_refresh)
        v.addLayout(row)

        self._poll = QTimer(self)
        self._poll.setInterval(700)
        self._poll.timeout.connect(self._tick)

        self.refresh()

    # -- table -------------------------------------------------------------
    def refresh(self) -> None:
        active = (self.cfg.get("stt_backend"), self.cfg.get("stt_model"))
        rows = dl.status()
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            name_item = QTableWidgetItem(r["name"])
            name_item.setData(Qt.UserRole, r["name"])
            self.table.setItem(i, 0, name_item)
            self.table.setItem(i, 1, QTableWidgetItem(r["backend"]))
            size = f"{r['size_mb'] / 1024:.1f} GB" if r["size_mb"] >= 1024 \
                else (f"{r['size_mb']} MB" if r["size_mb"] else "-")
            self.table.setItem(i, 2, QTableWidgetItem(size))
            if (r["backend"], r["name"]) == active:
                state = "Active" + ("" if r["installed"] else " - not downloaded")
            else:
                state = "Downloaded" if r["installed"] else "Not downloaded"
            self.table.setItem(i, 3, QTableWidgetItem(state))
        self._sync_buttons()

    def _selected(self) -> str:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            return ""
        item = self.table.item(min(rows), 0)
        return item.data(Qt.UserRole) if item else ""

    def _sync_buttons(self) -> None:
        busy = bool(self._downloading) or self._benchmarking
        has = bool(self._selected())
        self.btn_download.setEnabled(has and not busy)
        self.btn_use.setEnabled(has and not busy)
        self.btn_test.setEnabled(has and not busy)
        self.btn_refresh.setEnabled(not busy)

    # -- benchmark ---------------------------------------------------------
    def _benchmark(self) -> None:
        name = self._selected()
        if not name or self._benchmarking:
            return
        from ..bench import probe_specs
        if not dl._looks_installed(dl.catalog().get(name, {})):
            if QMessageBox.question(
                    self, "Test on this PC",
                    f"{name} is not downloaded yet. Testing it will download it "
                    "first. Continue?") != QMessageBox.Yes:
                return

        self._benchmarking = True
        self._sync_buttons()
        self.progress.setRange(0, 0)            # indeterminate
        self.progress.setVisible(True)
        self.status.setText(
            f"Measuring {name} on this PC ({probe_specs().summary()}) - "
            "transcribing 30 seconds of audio, please wait\u2026")

        self._bench_thread = QThread(self)
        self._bench_worker = _BenchWorker(name, self.cfg.get("win_compute_device", "auto"),
                                          self.cfg.get("win_compute_type", "auto"))
        self._bench_worker.moveToThread(self._bench_thread)
        self._bench_thread.started.connect(self._bench_worker.run)
        self._bench_worker.done.connect(self._bench_done)
        self._bench_worker.failed.connect(self._bench_failed)
        self._bench_thread.start()

    def _bench_cleanup(self) -> None:
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        if self._bench_thread is not None:
            self._bench_thread.quit()
            self._bench_thread.wait(5000)
        self._bench_thread = None
        self._bench_worker = None
        self._benchmarking = False
        self._sync_buttons()

    def _bench_done(self, payload: dict) -> None:
        r, i = payload["result"], payload["interpretation"]
        self._bench_cleanup()
        if not r.ok:
            QMessageBox.warning(self, "Test failed", r.error or "unknown error")
            return
        lines = [i["headline"], ""]
        if i.get("speed"):
            lines.append(f"Speed: {i['speed']} ({r.backend} on {r.device})")
        lines += ["", i["dictation"], "", i["meetings"]]
        for w in i.get("warnings", []):
            lines += ["", "\u26a0 " + w]
        self.status.setText(f"{r.model}: {i['headline']} ({i.get('speed', '')})")

        box = QMessageBox(self)
        box.setWindowTitle(f"{r.model} on this PC")
        box.setIcon(QMessageBox.Information if i["verdict"] in ("excellent", "good")
                    else QMessageBox.Warning)
        box.setText(i["headline"])
        box.setInformativeText("\n".join(lines[1:]).strip())
        box.setDetailedText(
            f"machine: {r.specs.summary()}\n"
            f"cpu: {r.specs.cpu}\n"
            f"real-time factor: {r.rtf}\n"
            f"model load: {r.load_seconds}s\n"
            f"sample: {r.sample_seconds:.0f}s of speech-shaped audio")
        box.exec()

    def _bench_failed(self, message: str) -> None:
        self._bench_cleanup()
        QMessageBox.warning(self, "Test failed", message)

    # -- download ----------------------------------------------------------
    def _download(self) -> None:
        name = self._selected()
        if not name or self._downloading:
            return
        need = dl.expected_bytes(name) / 1048576
        if need > 1024 and QMessageBox.question(
                self, "Download model",
                f"{name} is about {need / 1024:.1f} GB. Download it now?"
        ) != QMessageBox.Yes:
            return

        self._downloading = name
        self._baseline = dl.bytes_on_disk(name)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.status.setText(f"Downloading {name}… this can take several minutes.")
        self._sync_buttons()

        self._thread = QThread(self)
        self._worker = _Worker(name, self.cfg.get("win_compute_device", "auto"),
                               self.cfg.get("win_compute_type", "auto"))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._finished)
        self._worker.failed.connect(self._errored)
        self._thread.start()
        self._poll.start()

    def _tick(self) -> None:
        if not self._downloading:
            return
        got = max(0, dl.bytes_on_disk(self._downloading) - self._baseline)
        total = dl.expected_bytes(self._downloading)
        if total:
            pct = min(99, int(got * 100 / total))
            self.progress.setValue(pct)
            self.status.setText(
                f"Downloading {self._downloading}… {got / 1048576:.0f} of "
                f"about {total / 1048576:.0f} MB")
        else:
            self.progress.setRange(0, 0)

    def _cleanup(self) -> None:
        self._poll.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self._downloading = ""
        self._sync_buttons()

    def _finished(self, info: dict) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        name = info.get("name", "")
        self.status.setText(
            f"{name} is ready ({info.get('seconds', 0)}s). It is stored on this PC and "
            "will not download again.")
        self._cleanup()
        self.refresh()
        QTimer.singleShot(4000, lambda: self.progress.setVisible(False))

    def _errored(self, message: str) -> None:
        self.progress.setVisible(False)
        self.status.setText("")
        self._cleanup()
        QMessageBox.warning(self, "Download failed", message)
        self.refresh()

    # -- other actions -----------------------------------------------------
    def _use(self) -> None:
        name = self._selected()
        if not name:
            return
        meta = dl.catalog().get(name, {})
        backend = meta.get("backend", "whisper")
        self.cfg.set("stt_backend", backend)
        self.cfg.set("stt_model", name)
        self.model_activated.emit(backend, name)
        if not dl._looks_installed(meta):
            self.status.setText(
                f"{name} is now selected but not downloaded yet. Press Download, "
                "or the first dictation will stall while it fetches.")
        else:
            self.status.setText(f"{name} is now used for dictation.")
        self.refresh()

    def _open_folder(self) -> None:
        from .. import paths
        folder = paths.models_dir()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))                       # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", str(folder)], check=False)
            else:
                subprocess.run(["xdg-open", str(folder)], check=False)
        except Exception:
            log.exception("could not open the models folder")
            QMessageBox.information(self, "Models folder", str(folder))

    def closing(self) -> bool:
        """False if a download is in flight and the user chose to wait."""
        if not self._downloading:
            return True
        return QMessageBox.question(
            self, "Download in progress",
            f"{self._downloading} is still downloading. Close anyway?\n\n"
            "The download will continue in the background."
        ) == QMessageBox.Yes
