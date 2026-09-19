"""Start at sign-in, via the per-user Run key.

HKCU\\...\\Run needs no admin rights, survives updates and is what the user can
see and remove themselves. A Scheduled Task would run earlier but needs
elevation to create, which is a worse trade for a dictation app.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Muesli"
IS_WINDOWS = sys.platform == "win32"


def _command() -> str:
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return f'"{exe}" --tray'
    script = Path(__file__).resolve().parent.parent / "run_muesli.py"
    return f'"{exe}" "{script}" --tray'


def is_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        log.exception("could not read the Run key")
        return False


def set_enabled(enabled: bool) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if enabled:
                winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(k, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        log.exception("could not write the Run key")
        return False


def sync(cfg) -> None:
    want = bool(cfg.get("launch_at_login", True))
    if want != is_enabled():
        set_enabled(want)
