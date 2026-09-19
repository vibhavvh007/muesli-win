"""Settings window.

Every control writes straight back to Config, which saves atomically, so there
is no Apply button to forget. Changes that need a model reload or a new hotkey
are picked up by the controller through its config subscription.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..audio.capture import list_input_devices, list_loopback_devices
from ..config import VK, VK_NAME
from ..stt.registry import catalog
from ..text import llm
from ..winput.hook import layout_has_altgr
from . import icons

log = logging.getLogger(__name__)

LANGUAGES = [("auto", "Auto-detect"), ("en", "English"), ("hi", "Hindi"),
             ("de", "German"), ("fr", "French"), ("es", "Spanish"), ("it", "Italian"),
             ("nl", "Dutch"), ("pt", "Portuguese"), ("ja", "Japanese"),
             ("zh", "Chinese"), ("ar", "Arabic"), ("ta", "Tamil"), ("kn", "Kannada")]


class SettingsWindow(QDialog):
    changed = Signal(str)

    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Muesli Settings")
        self.setWindowIcon(icons.app_icon())
        self.resize(720, 620)

        tabs = QTabWidget(self)
        tabs.addTab(self._tab_dictation(), "Dictation")
        tabs.addTab(self._tab_models(), "Models")
        tabs.addTab(self._tab_dictionary(), "Dictionary")
        tabs.addTab(self._tab_meetings(), "Meetings")
        tabs.addTab(self._tab_interface(), "Interface")
        tabs.addTab(self._tab_advanced(), "Advanced")

        root = QVBoxLayout(self)
        root.addWidget(tabs)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)

    # -- helpers -----------------------------------------------------------
    def _bind_check(self, key: str, label: str) -> QCheckBox:
        c = QCheckBox(label)
        c.setChecked(bool(self.cfg.get(key)))
        c.toggled.connect(lambda v, k=key: self._set(k, bool(v)))
        return c

    def _bind_spin(self, key: str, lo: int, hi: int, suffix: str = "") -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(int(self.cfg.get(key) or 0))
        if suffix:
            s.setSuffix(suffix)
        s.valueChanged.connect(lambda v, k=key: self._set(k, int(v)))
        return s

    def _bind_line(self, key: str, password: bool = False) -> QLineEdit:
        e = QLineEdit(str(self.cfg.get(key) or ""))
        if password:
            e.setEchoMode(QLineEdit.Password)
        e.editingFinished.connect(lambda k=key, w=e: self._set(k, w.text().strip()))
        return e

    def _set(self, key: str, value) -> None:
        self.cfg.set(key, value)
        self.changed.emit(key)

    # -- tabs --------------------------------------------------------------
    def _tab_dictation(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.hotkey_box = QComboBox()
        for name in ("Right Alt", "Right Ctrl", "Left Ctrl", "Right Shift", "Right Win",
                     "Caps Lock", "F13", "F14", "F15", "Scroll Lock", "Pause"):
            self.hotkey_box.addItem(name, VK[name])
        current = int(self.cfg.get("dictation_hotkey", {}).get("vk", VK["Right Alt"]))
        idx = self.hotkey_box.findData(current)
        self.hotkey_box.setCurrentIndex(idx if idx >= 0 else 0)
        self.hotkey_box.currentIndexChanged.connect(self._on_hotkey_changed)
        form.addRow("Dictation hotkey", self.hotkey_box)

        self.altgr_warning = QLabel()
        self.altgr_warning.setWordWrap(True)
        self.altgr_warning.setStyleSheet("color:#b23b3b;")
        form.addRow("", self.altgr_warning)
        self._refresh_altgr_warning()

        form.addRow("Hold threshold", self._bind_spin("hotkey_trigger_threshold_ms", 80, 1200, " ms"))
        form.addRow("", QLabel("Hold past the threshold to dictate; release to type."))
        form.addRow("", self._bind_check("enable_double_tap_dictation",
                                         "Double-tap for hands-free (tap once to stop)"))
        form.addRow("Double-tap window", self._bind_spin("double_tap_window_ms", 150, 1000, " ms"))
        form.addRow("Hands-free idle stop", self._bind_spin("idle_timeout", 0, 3600, " s"))
        form.addRow("Maximum length", self._bind_spin("max_dictation_seconds", 30, 7200, " s"))

        form.addRow(QLabel(""))
        form.addRow("", self._bind_check("mute_system_audio_during_dictation",
                                         "Mute other apps while dictating"))
        form.addRow("", self._bind_check("sound_enabled", "Play start and stop sounds"))
        form.addRow("", self._bind_check("strip_filler_words", "Remove filler words (um, uh, er)"))
        form.addRow("", self._bind_check("enable_scratch_that", "Honour “scratch that”"))

        form.addRow(QLabel(""))
        form.addRow("", self._bind_check(
            "enable_quill", "Quill \u2013 select text, speak an instruction, rewrite it"))
        quill_key = QComboBox()
        for name in ("Right Ctrl", "Right Shift", "Right Win", "F13", "F14", "Pause"):
            quill_key.addItem(name, VK[name])
        i = quill_key.findData(int(self.cfg.get("quill_hotkey", {}).get("vk", VK["Right Ctrl"])))
        quill_key.setCurrentIndex(max(0, i))
        quill_key.currentIndexChanged.connect(
            lambda _i, x=quill_key: self._set(
                "quill_hotkey", {"vk": int(x.currentData()),
                                 "label": VK_NAME.get(int(x.currentData()), "?")}))
        form.addRow("Quill hotkey", quill_key)
        form.addRow("", QLabel("Quill needs a post-processing model configured "
                               "under Models."))

        form.addRow(QLabel(""))
        dev = QComboBox()
        dev.addItem("System default", "")
        for d in list_input_devices():
            dev.addItem(d.label(), d.name)
        i = dev.findData(self.cfg.get("win_audio_input_device", ""))
        dev.setCurrentIndex(i if i >= 0 else 0)
        dev.currentIndexChanged.connect(
            lambda _i, w=dev: self._set("win_audio_input_device", w.currentData()))
        form.addRow("Microphone", dev)
        return w

    def _on_hotkey_changed(self) -> None:
        vk = self.hotkey_box.currentData()
        self._set("dictation_hotkey", {"vk": int(vk), "label": VK_NAME.get(int(vk), "?")})
        self._refresh_altgr_warning()

    def _refresh_altgr_warning(self) -> None:
        vk = int(self.cfg.get("dictation_hotkey", {}).get("vk", VK["Right Alt"]))
        if vk == VK["Right Alt"] and layout_has_altgr():
            self.altgr_warning.setText(
                "Your keyboard layout uses Right Alt as AltGr. While Muesli holds that "
                "key you will not be able to type AltGr characters (@ \\ € …). "
                "Choose Right Ctrl or F13 instead."
            )
        else:
            self.altgr_warning.setText("")

    def _tab_models(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        g = QGroupBox("Dictation")
        f = QFormLayout(g)
        self.backend_box = QComboBox()
        for b in ("whisper", "parakeet", "openai", "openrouter", "groq"):
            self.backend_box.addItem(b, b)
        i = self.backend_box.findData(self.cfg.get("stt_backend", "whisper"))
        self.backend_box.setCurrentIndex(max(0, i))
        self.backend_box.currentIndexChanged.connect(self._on_backend_changed)
        f.addRow("Engine", self.backend_box)

        self.model_box = QComboBox()
        self._fill_models()
        self.model_box.currentIndexChanged.connect(
            lambda _i: self._set("stt_model", self.model_box.currentData()))
        f.addRow("Model", self.model_box)

        lang = QComboBox()
        for code, name in LANGUAGES:
            lang.addItem(name, code)
        i = lang.findData(self.cfg.get("whisper_language", "auto"))
        lang.setCurrentIndex(max(0, i))
        lang.currentIndexChanged.connect(
            lambda _i, x=lang: self._set("whisper_language", x.currentData()))
        f.addRow("Language", lang)

        comp = QComboBox()
        for c in ("auto", "cuda", "cpu"):
            comp.addItem(c, c)
        i = comp.findData(self.cfg.get("win_compute_device", "auto"))
        comp.setCurrentIndex(max(0, i))
        comp.currentIndexChanged.connect(
            lambda _i, x=comp: self._set("win_compute_device", x.currentData()))
        f.addRow("Compute", comp)
        f.addRow("", QLabel("Models download on first use. Nothing leaves this PC unless "
                            "you pick a hosted engine."))
        v.addWidget(g)

        g2 = QGroupBox("Post-processing (optional LLM cleanup)")
        f2 = QFormLayout(g2)
        f2.addRow("", self._bind_check("enable_post_processor", "Enable post-processing"))
        pb = QComboBox()
        for b in ("local", "ollama", "lmstudio", "openai", "openrouter", "custom"):
            pb.addItem(b, b)
        i = pb.findData(self.cfg.get("post_processor_backend", "local"))
        pb.setCurrentIndex(max(0, i))
        pb.currentIndexChanged.connect(
            lambda _i, x=pb: self._set("post_processor_backend", x.currentData()))
        f2.addRow("Backend", pb)
        f2.addRow("Ollama URL", self._bind_line("ollama_url"))
        f2.addRow("Ollama model", self._bind_line("ollama_model"))
        test = QPushButton("Test connection")
        test.clicked.connect(self._test_llm)
        f2.addRow("", test)
        prompt = QPlainTextEdit(str(self.cfg.get("post_processor_system_prompt", "")))
        prompt.setFixedHeight(120)
        prompt.textChanged.connect(
            lambda: self._set("post_processor_system_prompt", prompt.toPlainText()))
        f2.addRow("Prompt", prompt)
        v.addWidget(g2)

        g3 = QGroupBox("API keys (stored in your config.json)")
        f3 = QFormLayout(g3)
        f3.addRow("OpenAI", self._bind_line("openai_api_key", password=True))
        f3.addRow("OpenRouter", self._bind_line("openrouter_api_key", password=True))
        v.addWidget(g3)
        v.addStretch(1)
        return w

    def _fill_models(self) -> None:
        backend = self.cfg.get("stt_backend", "whisper")
        self.model_box.blockSignals(True)
        self.model_box.clear()
        for name, meta in catalog().items():
            if meta["backend"] != backend:
                continue
            size = f"  ({meta['size_mb'] / 1024:.1f} GB)" if meta["size_mb"] else ""
            self.model_box.addItem(f"{name}{size}", name)
        i = self.model_box.findData(self.cfg.get("stt_model"))
        if i >= 0:
            self.model_box.setCurrentIndex(i)
        elif self.model_box.count():
            self.model_box.setCurrentIndex(0)
            self._set("stt_model", self.model_box.currentData())
        self.model_box.blockSignals(False)

    def _on_backend_changed(self) -> None:
        self._set("stt_backend", self.backend_box.currentData())
        self._fill_models()

    def _test_llm(self) -> None:
        target = llm.target_from_config(self.cfg, "post")
        ok, msg = llm.probe(target)
        box = QMessageBox(self)
        box.setWindowTitle("Post-processor")
        box.setIcon(QMessageBox.Information if ok else QMessageBox.Warning)
        box.setText(f"{target.backend} / {target.model or '(no model)'}")
        box.setInformativeText(("Reachable. Replied: " + msg) if ok else msg)
        box.exec()

    def _tab_dictionary(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Words Muesli should always get right - product names, colleagues, jargon. "
            "Matching is fuzzy, so “ultra human” still corrects to “Ultrahuman”."))
        self.dict_table = QTableWidget(0, 3)
        self.dict_table.setHorizontalHeaderLabels(["Heard as", "Replace with", "Threshold"])
        self.dict_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.dict_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for e in self.cfg.get("custom_words", []) or []:
            self._add_dict_row(e.get("word", ""), e.get("replacement", ""),
                               e.get("matching_threshold", 0.85))
        v.addWidget(self.dict_table)

        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_dict_row("", "", 0.85))
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove_dict_row)
        save = QPushButton("Save dictionary")
        save.clicked.connect(self._save_dictionary)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        row.addWidget(save)
        v.addLayout(row)
        return w

    def _add_dict_row(self, word: str, repl: str, threshold: float) -> None:
        r = self.dict_table.rowCount()
        self.dict_table.insertRow(r)
        self.dict_table.setItem(r, 0, QTableWidgetItem(str(word)))
        self.dict_table.setItem(r, 1, QTableWidgetItem(str(repl)))
        self.dict_table.setItem(r, 2, QTableWidgetItem(str(threshold)))

    def _remove_dict_row(self) -> None:
        for idx in sorted({i.row() for i in self.dict_table.selectedIndexes()}, reverse=True):
            self.dict_table.removeRow(idx)

    def _save_dictionary(self) -> None:
        entries = []
        for r in range(self.dict_table.rowCount()):
            word = (self.dict_table.item(r, 0).text() if self.dict_table.item(r, 0) else "").strip()
            if not word:
                continue
            repl = (self.dict_table.item(r, 1).text() if self.dict_table.item(r, 1) else "").strip()
            try:
                th = float(self.dict_table.item(r, 2).text()) if self.dict_table.item(r, 2) else 0.85
            except ValueError:
                th = 0.85
            entries.append({"word": word, "replacement": repl or word,
                            "matching_threshold": max(0.5, min(1.0, th))})
        self._set("custom_words", entries)
        QMessageBox.information(self, "Dictionary", f"Saved {len(entries)} entries.")

    def _tab_meetings(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.addRow("", self._bind_check("enable_meeting_recording_hotkey",
                                      "Enable Ctrl+Shift+R to start/stop a meeting"))
        f.addRow("", self._bind_check("auto_record_meetings",
                                      "Start recording automatically when a call app is in front"))
        save = QComboBox()
        for v_, label in (("never", "Never keep audio (transcript only)"),
                          ("always", "Always keep the audio file")):
            save.addItem(label, v_)
        i = save.findData(self.cfg.get("meeting_recording_save_policy", "never"))
        save.setCurrentIndex(max(0, i))
        save.currentIndexChanged.connect(
            lambda _i, x=save: self._set("meeting_recording_save_policy", x.currentData()))
        f.addRow("Audio", save)

        loop = QComboBox()
        loop.addItem("Default output device", "")
        for d in list_loopback_devices():
            loop.addItem(d.label(), d.name)
        i = loop.findData(self.cfg.get("win_loopback_device", ""))
        loop.setCurrentIndex(max(0, i))
        loop.currentIndexChanged.connect(
            lambda _i, x=loop: self._set("win_loopback_device", x.currentData()))
        f.addRow("System audio", loop)

        sb = QComboBox()
        for b in ("openrouter", "openai", "ollama", "lmstudio", "custom"):
            sb.addItem(b, b)
        i = sb.findData(self.cfg.get("meeting_summary_backend", "openrouter"))
        sb.setCurrentIndex(max(0, i))
        sb.currentIndexChanged.connect(
            lambda _i, x=sb: self._set("meeting_summary_backend", x.currentData()))
        f.addRow("Notes engine", sb)
        f.addRow("Notes model", self._bind_line("meeting_summary_model"))

        from ..meetings.templates import choices
        tpl = QComboBox()
        for tid, name in choices():
            tpl.addItem(name, tid)
        i = tpl.findData(self.cfg.get("default_meeting_template_id", "auto"))
        tpl.setCurrentIndex(max(0, i))
        tpl.currentIndexChanged.connect(
            lambda _i, x=tpl: self._set("default_meeting_template_id", x.currentData()))
        f.addRow("Default template", tpl)

        f.addRow("", self._bind_check("auto_export_markdown_enabled",
                                      "Export notes to Documents\\Muesli when a meeting ends"))
        f.addRow("", self._bind_check("meeting_hook_enabled", "Run a program after each meeting"))
        hook_row = QWidget()
        hl = QHBoxLayout(hook_row)
        hl.setContentsMargins(0, 0, 0, 0)
        hook_edit = self._bind_line("meeting_hook_path")
        browse = QPushButton("Browse…")

        def pick_hook() -> None:
            p, _ = QFileDialog.getOpenFileName(self, "Choose a hook program")
            if p:
                hook_edit.setText(p)
                self._set("meeting_hook_path", p)
        browse.clicked.connect(pick_hook)
        hl.addWidget(hook_edit)
        hl.addWidget(browse)
        f.addRow("Hook", hook_row)
        return w

    def _tab_interface(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.addRow("", self._bind_check("show_floating_indicator", "Show the floating indicator"))
        f.addRow("", self._bind_check("show_hotkey_on_floating_indicator",
                                      "Show the hotkey on the indicator"))
        f.addRow("", self._bind_check("show_hotkey_in_menu_bar", "Show the hotkey in the tray tip"))
        f.addRow("", self._bind_check("launch_at_login", "Start Muesli when I sign in"))
        f.addRow("", self._bind_check("open_dashboard_on_launch", "Open the dashboard at launch"))
        f.addRow("", self._bind_check("dark_mode", "Dark mode"))
        f.addRow("Indicator colour", self._bind_line("recording_color_hex"))
        reset = QPushButton("Reset indicator position")
        reset.clicked.connect(lambda: self._set("indicator_position", None))
        f.addRow("", reset)
        return w

    def _tab_advanced(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        method = QComboBox()
        for v_, label in (("auto", "Automatic (recommended)"),
                          ("sendinput", "Simulated keystrokes"),
                          ("clipboard", "Clipboard paste")):
            method.addItem(label, v_)
        i = method.findData(self.cfg.get("win_inject_method", "auto"))
        method.setCurrentIndex(max(0, i))
        method.currentIndexChanged.connect(
            lambda _i, x=method: self._set("win_inject_method", x.currentData()))
        f.addRow("Typing method", method)
        f.addRow("Clipboard restore delay",
                 self._bind_spin("win_clipboard_restore_delay_ms", 0, 5000, " ms"))
        f.addRow("", self._bind_check("win_suppress_hotkey_passthrough",
                                      "Hide the hotkey from other applications"))
        f.addRow("", self._bind_check("win_altgr_guard", "Warn when the hotkey clashes with AltGr"))

        lvl = QComboBox()
        for name in ("DEBUG", "INFO", "WARNING", "ERROR"):
            lvl.addItem(name, name)
        i = lvl.findData(self.cfg.get("win_log_level", "INFO"))
        lvl.setCurrentIndex(max(0, i))
        lvl.currentIndexChanged.connect(
            lambda _i, x=lvl: self._set("win_log_level", x.currentData()))
        f.addRow("Log level", lvl)

        imp = QPushButton("Import settings from a Mac config.json…")
        imp.clicked.connect(self._import_config)
        f.addRow("", imp)
        f.addRow("", QLabel("API keys already stored here are never overwritten by an import."))
        return w

    def _import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose config.json", "",
                                              "JSON (*.json)")
        if not path:
            return
        try:
            n = self.cfg.merge_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        QMessageBox.information(
            self, "Imported",
            f"Merged {n} settings. Restart Muesli for the model and hotkey to take effect.")
        self.changed.emit("*")
