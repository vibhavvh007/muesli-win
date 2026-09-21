"""Type transcribed text into whatever window has focus.

Two strategies, because neither alone is reliable everywhere:

  SendInput + KEYEVENTF_UNICODE  - works in almost every app, preserves undo
                                   granularity, but is slow for long text and
                                   some apps drop fast synthetic input.
  Clipboard + Ctrl+V             - instant regardless of length, but clobbers
                                   the clipboard (we save and restore it) and a
                                   few apps block programmatic paste.

`auto` uses SendInput for short text and the clipboard for long text, and falls
back from one to the other on failure.

Known limit, stated rather than worked around: Windows UIPI forbids a normal
process sending input to a window running elevated. If the focused app is
elevated, nothing will be typed. Muesli must then also run elevated.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes

log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_RETURN, VK_TAB, VK_CONTROL, VK_V = 0x0D, 0x09, 0x11, 0x56
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# Above this many characters, synthetic keystrokes get slow and lossy.
SENDINPUT_MAX_CHARS = 220
CHUNK = 40


if IS_WINDOWS:
    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_byte * 24)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]
else:                                                   # import-safe off Windows
    INPUT = None


class TextInjector:
    def __init__(self, method: str = "auto", clipboard_restore_delay_ms: int = 400) -> None:
        self.method = method
        self.clipboard_restore_delay_ms = clipboard_restore_delay_ms
        self.last_error = 0
        self.stranded_on_clipboard = False
        self._user32 = ctypes.WinDLL("user32", use_last_error=True) if IS_WINDOWS else None
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if IS_WINDOWS else None
        if IS_WINDOWS:
            # Declared rather than left to ctypes' defaults: the array pointer and
            # the size must be passed exactly or SendInput silently inserts nothing.
            self._user32.SendInput.argtypes = [wintypes.UINT,
                                               ctypes.POINTER(INPUT),
                                               ctypes.c_int]
            self._user32.SendInput.restype = wintypes.UINT

    # -- public ------------------------------------------------------------
    def inject(self, text: str) -> bool:
        if not text:
            return True
        if not IS_WINDOWS:
            log.info("[dry-run] would type: %s", text[:120])
            return True

        self.last_error = 0
        method = self.method
        if method == "auto":
            method = "clipboard" if len(text) > SENDINPUT_MAX_CHARS else "sendinput"

        order = ("clipboard", "sendinput") if method == "clipboard" \
            else ("sendinput", "clipboard")
        for how in order:
            if (self._paste(text) if how == "clipboard" else self._type(text)):
                return True
            log.warning("%s injection failed; trying the next method", how)

        # Everything failed. Leave the text on the clipboard so the dictation is
        # recoverable with one Ctrl+V - losing a user's words is the one outcome
        # worth avoiding at all costs.
        self.stranded_on_clipboard = self._clipboard_set(text)
        log.error("could not type into the focused window (win32 error %d); "
                  "text %s left on the clipboard", self.last_error,
                  "was" if self.stranded_on_clipboard else "could NOT be")
        return False

    # -- SendInput ---------------------------------------------------------
    def _type(self, text: str) -> bool:
        ok = True
        for i in range(0, len(text), CHUNK):
            chunk = text[i:i + CHUNK]
            events: list = []
            for ch in chunk:
                if ch == "\n":
                    events += self._vk_events(VK_RETURN)
                elif ch == "\r":
                    continue
                elif ch == "\t":
                    events += self._vk_events(VK_TAB)
                else:
                    for unit in _utf16_units(ch):
                        events += self._unicode_events(unit)
            if events and not self._send(events):
                ok = False
                break
            time.sleep(0.001)                   # let the target app drain its queue
        return ok

    def _unicode_events(self, unit: int) -> list:
        down = INPUT(type=INPUT_KEYBOARD)
        down.ki = _KEYBDINPUT(wVk=0, wScan=unit, dwFlags=KEYEVENTF_UNICODE, time=0,
                              dwExtraInfo=None)
        up = INPUT(type=INPUT_KEYBOARD)
        up.ki = _KEYBDINPUT(wVk=0, wScan=unit,
                            dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0,
                            dwExtraInfo=None)
        return [down, up]

    def _vk_events(self, vk: int) -> list:
        down = INPUT(type=INPUT_KEYBOARD)
        down.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)
        up = INPUT(type=INPUT_KEYBOARD)
        up.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
        return [down, up]

    def _send(self, events: list) -> bool:
        ok, _err = self._send_ex(events)
        return ok

    def _send_ex(self, events: list) -> tuple[bool, int]:
        """-> (ok, win32 error). Retries once: a focus change in flight makes
        SendInput fail transiently, and one retry costs 120 ms."""
        arr = (INPUT * len(events))(*events)
        for attempt in (0, 1):
            ctypes.set_last_error(0)
            sent = self._user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
            if sent == len(events):
                return True, 0
            err = ctypes.get_last_error()
            self.last_error = err
            log.error("SendInput sent %d/%d (win32 error %d)%s", sent, len(events), err,
                      " - retrying" if attempt == 0 else "")
            if attempt == 0:
                time.sleep(0.12)
        return False, self.last_error

    # -- clipboard ---------------------------------------------------------
    def _paste(self, text: str) -> bool:
        saved = self._clipboard_get()
        if not self._clipboard_set(text):
            return False
        try:
            ev = self._vk_down(VK_CONTROL) + self._vk_events(VK_V) + self._vk_up(VK_CONTROL)
            if not self._send(ev):
                return False
        finally:
            if saved is not None:
                delay = max(self.clipboard_restore_delay_ms, 50) / 1000.0
                _defer(delay, lambda: self._clipboard_set(saved))
        return True

    def _vk_down(self, vk: int) -> list:
        e = INPUT(type=INPUT_KEYBOARD)
        e.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)
        return [e]

    def _vk_up(self, vk: int) -> list:
        e = INPUT(type=INPUT_KEYBOARD)
        e.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
        return [e]

    def _open_clipboard(self, attempts: int = 12) -> bool:
        """Another process may hold the clipboard; retry briefly rather than fail."""
        for _ in range(attempts):
            if self._user32.OpenClipboard(None):
                return True
            time.sleep(0.02)
        log.warning("could not open clipboard")
        return False

    def _clipboard_get(self) -> str | None:
        if not self._open_clipboard():
            return None
        try:
            handle = self._user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            self._kernel32.GlobalLock.restype = ctypes.c_void_p
            ptr = self._kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.c_wchar_p(ptr).value or ""
            finally:
                self._kernel32.GlobalUnlock(handle)
        except Exception:
            log.exception("clipboard read failed")
            return None
        finally:
            self._user32.CloseClipboard()

    def _clipboard_set(self, text: str) -> bool:
        if not self._open_clipboard():
            return False
        try:
            self._user32.EmptyClipboard()
            buf = ctypes.create_unicode_buffer(text)
            size = ctypes.sizeof(buf)
            self._kernel32.GlobalAlloc.restype = ctypes.c_void_p
            h = self._kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not h:
                return False
            self._kernel32.GlobalLock.restype = ctypes.c_void_p
            ptr = self._kernel32.GlobalLock(h)
            ctypes.memmove(ptr, ctypes.byref(buf), size)
            self._kernel32.GlobalUnlock(h)
            if not self._user32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(h)):
                self._kernel32.GlobalFree(ctypes.c_void_p(h))
                return False
            return True
        except Exception:
            log.exception("clipboard write failed")
            return False
        finally:
            self._user32.CloseClipboard()


def _utf16_units(ch: str) -> list[int]:
    """Characters above the BMP (emoji) must go as a UTF-16 surrogate pair."""
    cp = ord(ch)
    if cp <= 0xFFFF:
        return [cp]
    cp -= 0x10000
    return [0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)]


def _defer(seconds: float, fn) -> None:
    import threading
    t = threading.Timer(seconds, fn)
    t.daemon = True
    t.start()


# -- focused window info, used for history and screen context ---------------
def foreground_window_info() -> tuple[str, str]:
    """(process name, window title) of the focused window; ('','') off Windows."""
    if not IS_WINDOWS:
        return "", ""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        name = ""
        if h:
            try:
                size = wintypes.DWORD(260)
                nbuf = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(h, 0, nbuf, ctypes.byref(size)):
                    name = nbuf.value.rsplit("\\", 1)[-1]
            finally:
                kernel32.CloseHandle(h)
        return name, title
    except Exception:
        log.exception("foreground window probe failed")
        return "", ""


# --- why did injection fail? ------------------------------------------------
# Windows UIPI blocks synthetic input from a lower integrity process to a higher
# one. The old message asserted that was the cause without checking, which sends
# someone to "run as administrator" even when elevation is not the problem.

TOKEN_QUERY = 0x0008
TokenIntegrityLevel = 25
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

INTEGRITY_NAMES = {0x0000: "untrusted", 0x1000: "low", 0x2000: "medium",
                   0x3000: "high", 0x4000: "system"}


def _integrity_of(handle) -> int | None:
    """Integrity RID for an open process handle, or None if unreadable."""
    if not IS_WINDOWS:
        return None
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        size = wintypes.DWORD(0)
        advapi.GetTokenInformation(token, TokenIntegrityLevel, None, 0,
                                   ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, TokenIntegrityLevel, buf,
                                          size, ctypes.byref(size)):
            return None
        # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES { PSID Sid; DWORD Attributes } }
        sid = ctypes.c_void_p.from_buffer(buf).value
        advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        advapi.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
        count = advapi.GetSidSubAuthorityCount(ctypes.c_void_p(sid)).contents.value
        return int(advapi.GetSidSubAuthority(ctypes.c_void_p(sid), count - 1).contents.value)
    except Exception:
        log.debug("integrity probe failed", exc_info=True)
        return None
    finally:
        kernel32.CloseHandle(token)


def own_integrity() -> int | None:
    if not IS_WINDOWS:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _integrity_of(kernel32.GetCurrentProcess())


def foreground_integrity() -> tuple[int | None, str]:
    """(integrity RID, process name) of the focused window."""
    if not IS_WINDOWS:
        return None, ""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None, foreground_window_info()[0]
    try:
        return _integrity_of(handle), foreground_window_info()[0]
    finally:
        kernel32.CloseHandle(handle)


def diagnose_injection_failure(win32_error: int = 0,
                               on_clipboard: bool = False) -> str:
    """A message that reflects what is actually wrong."""
    recovery = (" Your text is on the clipboard - press Ctrl+V to paste it."
                if on_clipboard else
                " The text is saved in the Muesli dashboard.")

    app, _title = foreground_window_info()
    where = f" ({app})" if app else ""
    mine = own_integrity()
    theirs, _ = foreground_integrity()

    if mine is not None and theirs is not None and theirs > mine:
        return (f"{INTEGRITY_NAMES.get(theirs, 'that')}-integrity window{where} "
                "refused input from Muesli. Windows blocks this unless Muesli "
                "runs with the same privileges - right-click Muesli and choose "
                "Run as administrator." + recovery)

    if win32_error == 5:
        return ("Windows refused the keystrokes (access denied)" + where +
                ". This is usually an elevated or protected window." + recovery)

    if win32_error:
        return (f"Windows rejected the keystrokes (error {win32_error})" + where +
                "." + recovery)

    return ("Could not type into the focused window" + where +
            ". Try switching Typing method to Clipboard paste in "
            "Settings > Advanced." + recovery)
