"""One Muesli at a time.

Two instances would fight over the hotkey and both would type. A named mutex is
the Windows-native way to prevent that; a lock file covers everything else.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

MUTEX_NAME = "Global\\MuesliForWindows_SingleInstance"


class SingleInstance:
    def __init__(self) -> None:
        self._handle = None
        self._fh = None

    def acquire(self) -> bool:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            self._handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
            ERROR_ALREADY_EXISTS = 183
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                log.warning("another instance is already running")
                return False
            return bool(self._handle)

        from . import paths
        paths.ensure_dirs()
        lock = paths.app_support_dir() / "muesli.lock"
        try:
            import fcntl
            self._fh = open(lock, "w")
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, ImportError):
            return False

    def release(self) -> None:
        if self._handle is not None and sys.platform == "win32":
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(self._handle)
            self._handle = None
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
