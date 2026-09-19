"""Filesystem locations.

Mirrors Muesli's macOS layout so a config.json copied off a Mac drops straight in:
  macOS  ~/Library/Application Support/Muesli/config.json
  Windows %APPDATA%\\Muesli\\config.json

Models are deliberately kept out of %APPDATA% (which roams on domain-joined
machines) and land in %LOCALAPPDATA% instead.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Muesli"


def _env_dir(var: str, fallback: Path) -> Path:
    raw = os.environ.get(var)
    return Path(raw) if raw else fallback


def app_support_dir() -> Path:
    """Roaming config + database."""
    if sys.platform == "win32":
        base = _env_dir("APPDATA", Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = _env_dir("XDG_CONFIG_HOME", Path.home() / ".config")
    return base / APP_NAME


def cache_dir() -> Path:
    """Non-roaming: models, temp audio."""
    if sys.platform == "win32":
        base = _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        return base / APP_NAME / "cache"
    if sys.platform == "darwin":
        return Path.home() / ".cache" / "muesli"
    return _env_dir("XDG_CACHE_HOME", Path.home() / ".cache") / "muesli"


def config_path() -> Path:
    return app_support_dir() / "config.json"


def db_path() -> Path:
    return app_support_dir() / "muesli.db"


def models_dir() -> Path:
    return cache_dir() / "models"


def logs_dir() -> Path:
    return app_support_dir() / "logs"


def recordings_dir() -> Path:
    return cache_dir() / "recordings"


def exports_dir() -> Path:
    if sys.platform == "win32":
        docs = _env_dir("USERPROFILE", Path.home()) / "Documents"
    else:
        docs = Path.home() / "Documents"
    return docs / APP_NAME


def ensure_dirs() -> None:
    for d in (app_support_dir(), cache_dir(), models_dir(), logs_dir(), recordings_dir()):
        d.mkdir(parents=True, exist_ok=True)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Bundled read-only assets (sounds, icons), PyInstaller-aware."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent / "assets"
