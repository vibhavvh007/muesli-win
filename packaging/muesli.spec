# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: builds Muesli.exe (windowed) and muesli-cli.exe (console).

One-dir, not one-file. One-file unpacks ~200 MB to a temp folder on every
launch, which makes a hotkey app feel broken on first press; one-dir starts
immediately and the installer hides the folder anyway.

Models are NOT bundled - they download on first use into %LOCALAPPDATA%, the
same way macOS Muesli does. That keeps the installer around 120 MB instead of 4 GB.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "packaging" / "muesli.ico")

hidden = []
hidden += collect_submodules("muesli_win")
# Imported lazily by name, so PyInstaller cannot see them in the import graph.
for mod in ("faster_whisper", "ctranslate2", "onnxruntime", "onnx_asr", "sounddevice",
            "soxr", "pyaudiowpatch", "pycaw", "comtypes", "reportlab", "soundfile",
            "requests"):
    try:
        hidden += collect_submodules(mod)
    except Exception:
        pass

binaries = []
for mod in ("ctranslate2", "onnxruntime", "sounddevice", "soxr", "pyaudiowpatch"):
    try:
        binaries += collect_dynamic_libs(mod)
    except Exception:
        pass

# Data files, NOT just modules. collect_submodules finds importable code and
# nothing else, so a package that ships a model or a config beside its code
# gets silently left out of the bundle and fails at runtime with NO_SUCHFILE.
# faster-whisper ships faster_whisper/assets/silero_vad_v6.onnx, which is what
# vad_filter=True loads on the first transcription.
datas = []
for mod in ("faster_whisper", "onnx_asr", "onnxruntime", "ctranslate2", "soxr"):
    try:
        datas += collect_data_files(mod)
    except Exception:
        pass

# Qt modules we never use; dropping them saves ~90 MB.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick",
    "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtDesigner",
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "torch", "tensorflow",
]

gui_a = Analysis(
    [str(ROOT / "run_muesli.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=excludes,
    noarchive=False,
)
gui_pyz = PYZ(gui_a.pure)
gui_exe = EXE(
    gui_pyz, gui_a.scripts, [],
    exclude_binaries=True,
    name="Muesli",
    console=False,               # no console window for the tray app
    icon=ICON,
    version=str(ROOT / "packaging" / "version_info.txt"),
    disable_windowed_traceback=False,
)

cli_a = Analysis(
    [str(ROOT / "packaging" / "cli_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    excludes=excludes,
    noarchive=False,
)
cli_pyz = PYZ(cli_a.pure)
cli_exe = EXE(
    cli_pyz, cli_a.scripts, [],
    exclude_binaries=True,
    name="muesli-cli",
    console=True,                # JSON on stdout, for scripts and agents
    icon=ICON,
)

coll = COLLECT(
    gui_exe, gui_a.binaries, gui_a.datas,
    cli_exe, cli_a.binaries, cli_a.datas,
    strip=False, upx=False,      # UPX breaks CTranslate2/onnxruntime DLL loading
    name="Muesli",
)
