"""Regressions for the two things that were wrong in the UI:

  * no way to see or trigger a model download
  * no way to know settings had been saved, and a dictionary table that
    silently discarded edits unless a button was pressed
"""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from muesli_win.config import Config  # noqa: E402
from muesli_win.stt import download as dl  # noqa: E402
from muesli_win.stt import parakeet  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    return Config(tmp_path / "config.json").load()


@pytest.fixture
def win(cfg, tmp_path, monkeypatch):
    from muesli_win import paths
    monkeypatch.setattr(paths, "models_dir", lambda: tmp_path / "models")
    (tmp_path / "models").mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        QApplication([])
    from muesli_win.ui.settings import SettingsWindow
    return SettingsWindow(cfg)


# --- model sizes -------------------------------------------------------------
def test_parakeet_size_reflects_int8_not_fp32():
    """The repo's fp32 encoder is a 2.3 GB blob; int8 is what we actually fetch.

    Getting this wrong made the progress bar finish at ~26% and made the
    'installed' check report ready when a quarter of the model was present.
    """
    assert parakeet.size_mb("parakeet-tdt-0.6b-v3") == 640
    assert parakeet.size_mb("parakeet-tdt-0.6b-v3", None) > 2000


def test_parakeet_defaults_to_int8():
    t = parakeet.ParakeetTranscriber("parakeet-tdt-0.6b-v3")
    assert t.quantization == "int8"


def test_turbo_points_at_a_repo_that_resolves_directly():
    """mobiuslabsgmbh/ was transferred away and now only works via a redirect."""
    from muesli_win.stt import whisper_fw
    assert whisper_fw.MODELS["large-v3-turbo"] == "deepdml/faster-whisper-large-v3-turbo-ct2"


def test_catalog_covers_every_backend_model():
    cat = dl.catalog()
    from muesli_win.stt import whisper_fw
    for name in whisper_fw.MODELS:
        assert name in cat and cat[name]["backend"] == "whisper"
    for name in parakeet.MODELS:
        assert name in cat and cat[name]["backend"] == "parakeet"


def test_unknown_model_download_fails_clearly():
    from muesli_win.stt.base import TranscriptionError
    with pytest.raises(TranscriptionError) as e:
        dl.download("not-a-model")
    assert "unknown model" in str(e.value)


def test_installed_check_is_not_fooled_by_a_partial_download(tmp_path, monkeypatch):
    from muesli_win import paths
    monkeypatch.setattr(paths, "models_dir", lambda: tmp_path)
    meta = dl.catalog()["parakeet-tdt-0.6b-v3"]
    d = meta["dir"] / "models--istupakov--parakeet-tdt-0.6b-v3-onnx"
    d.mkdir(parents=True)
    (d / "partial.bin").write_bytes(b"\0" * 200 * 1024 * 1024)      # 200 of 640 MB
    assert dl._looks_installed(meta) is False


# --- settings persistence ----------------------------------------------------
def test_footer_confirms_a_save_happened(win):
    assert "automatically" in win.saved_label.text()
    win._set("dark_mode", True)
    assert "Saved" in win.saved_label.text()


def test_dictionary_edit_survives_closing_without_pressing_save(win, cfg):
    win._add_dict_row("ultrahuman", "Ultrahuman", 0.85)
    win.flush()
    assert cfg.get("custom_words") == [
        {"word": "ultrahuman", "replacement": "Ultrahuman", "matching_threshold": 0.85}
    ]


def test_dictionary_removal_also_persists(win, cfg):
    win._add_dict_row("zoho", "Zoho", 0.9)
    win.flush()
    assert len(cfg.get("custom_words")) == 1
    win.dict_table.selectRow(0)
    win._remove_dict_row()
    win.flush()
    assert cfg.get("custom_words") == []


def test_every_change_reaches_disk_immediately(win, cfg, tmp_path):
    win._set("hotkey_trigger_threshold_ms", 420)
    reloaded = Config(cfg.path).load()
    assert reloaded.get("hotkey_trigger_threshold_ms") == 420


def test_bad_threshold_text_does_not_lose_the_entry(win, cfg):
    from PySide6.QtWidgets import QTableWidgetItem
    win._add_dict_row("ind as", "Ind AS", 0.9)
    win.dict_table.setItem(0, 2, QTableWidgetItem("not a number"))
    win.flush()
    saved = cfg.get("custom_words")
    assert saved and saved[0]["word"] == "ind as"
    assert saved[0]["matching_threshold"] == 0.85          # fell back to the default


def test_models_panel_lists_models_and_marks_the_active_one(win, cfg):
    panel = win.models_panel
    assert panel.table.rowCount() == len(dl.catalog())
    cfg.set("stt_backend", "whisper")
    cfg.set("stt_model", "large-v3")
    panel.refresh()
    states = [panel.table.item(r, 3).text() for r in range(panel.table.rowCount())]
    assert any(s.startswith("Active") for s in states)


def test_activating_a_model_from_the_table_writes_config(win, cfg):
    panel = win.models_panel
    for r in range(panel.table.rowCount()):
        if panel.table.item(r, 0).text() == "parakeet-tdt-0.6b-v3":
            panel.table.selectRow(r)
            break
    panel._use()
    assert cfg.get("stt_backend") == "parakeet"
    assert cfg.get("stt_model") == "parakeet-tdt-0.6b-v3"
    # and it warns rather than pretending it is ready
    assert "not downloaded" in panel.status.text()


# --- window sizing (reported: buttons off-screen, window not resizable) ------
def test_settings_can_shrink_small_enough_for_a_scaled_laptop(win):
    """Reported from a real install: the footer buttons were unreachable.

    The Models tab gained a table in v1.0.1 and the layout's minimum height grew
    past the screen, so the Done / Show-config-file row sat below the bottom edge
    and the dialog refused to shrink. Each tab now scrolls instead.
    """
    hint = win.minimumSizeHint()
    assert hint.height() <= 560, f"minimum height {hint.height()} is too tall to fit"
    assert hint.width() <= 700, f"minimum width {hint.width()} is too wide"


def test_settings_is_user_resizable(win):
    assert win.isSizeGripEnabled()
    win.resize(520, 380)
    assert win.width() <= 720 and win.height() <= 640


def test_every_tab_is_inside_a_scroll_area(win):
    from PySide6.QtWidgets import QScrollArea, QTabWidget
    tabs = win.findChild(QTabWidget)
    assert tabs is not None and tabs.count() == 6
    for i in range(tabs.count()):
        assert isinstance(tabs.widget(i), QScrollArea), \
            f"tab {tabs.tabText(i)} is not scrollable"


def test_footer_controls_exist_and_are_outside_the_scrolled_area(win):
    """Done and the save indicator must stay visible whatever the tab height."""
    from PySide6.QtWidgets import QPushButton, QScrollArea
    buttons = [b.text() for b in win.findChildren(QPushButton)]
    assert "Done" in buttons
    assert "Show config file" in buttons
    done = next(b for b in win.findChildren(QPushButton) if b.text() == "Done")
    parent = done.parent()
    while parent is not None:
        assert not isinstance(parent, QScrollArea), "Done is inside a scroll area"
        parent = parent.parent()


def test_models_table_height_is_bounded(win):
    t = win.models_panel.table
    assert t.maximumHeight() <= 300, "an unbounded table is what pushed the footer off"
