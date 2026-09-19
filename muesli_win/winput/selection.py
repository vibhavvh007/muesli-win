"""Read the text currently selected in the focused app.

Windows has no general "give me the selection" API that works everywhere, so
the universal trick is to send Ctrl+C and read the clipboard - then put the
user's clipboard back exactly as it was. We detect "nothing was selected" by
checking whether the clipboard sequence number actually changed, which is more
reliable than comparing contents (copying the same text twice looks identical).
"""
from __future__ import annotations

import ctypes
import logging
import sys
import time

log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"

VK_CONTROL, VK_C = 0x11, 0x43


def clipboard_sequence() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        return int(ctypes.WinDLL("user32").GetClipboardSequenceNumber())
    except Exception:
        return 0


def read_selection(injector, timeout: float = 0.6) -> str:
    """-> the selected text, or "" if nothing was selected.

    `injector` supplies the clipboard and SendInput plumbing so the two
    components cannot drift apart.
    """
    if not IS_WINDOWS:
        return ""
    saved = injector._clipboard_get()
    before = clipboard_sequence()
    try:
        events = injector._vk_down(VK_CONTROL) + injector._vk_events(VK_C) \
            + injector._vk_up(VK_CONTROL)
        if not injector._send(events):
            return ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if clipboard_sequence() != before:
                break
            time.sleep(0.02)
        else:
            return ""                      # nothing copied -> nothing was selected
        return injector._clipboard_get() or ""
    except Exception:
        log.exception("could not read the selection")
        return ""
    finally:
        if saved is not None:
            try:
                injector._clipboard_set(saved)
            except Exception:
                log.warning("could not restore the clipboard")
