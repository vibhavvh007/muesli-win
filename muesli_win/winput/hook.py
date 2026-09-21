"""Global low-level keyboard hook (WH_KEYBOARD_LL).

Three things make or break this on Windows, and all three are handled here:

1. The hook callback runs on the thread that installed it, and that thread must
   pump messages. So the hook lives on its own thread with a GetMessage loop.
2. If the callback takes longer than LowLevelHooksTimeout (300 ms by default)
   Windows silently drops the hook and dictation dies with no error. The
   callback therefore does nothing but update a state machine and enqueue a
   callback onto a worker thread - never any audio, model or UI work.
3. The ctypes callback object must be kept alive for the process lifetime or it
   is garbage-collected and the process crashes inside user32.

Injected events (our own SendInput) are ignored so text we type cannot retrigger
the hotkey.
"""
from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

from .statemachine import Action, HotkeyStateMachine

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT, WM_TIMER = 0x0012, 0x0113
LLKHF_INJECTED = 0x10
HC_ACTION = 0

VK_ESCAPE = 0x1B
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LWIN, VK_RWIN = 0x5B, 0x5C

TICK_MS = 20            # resolution of the hold threshold while a press is pending


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


def layout_has_altgr() -> bool:
    """True if the active keyboard layout uses Right Alt as AltGr.

    On such layouts (German, Polish, most non-US European, and US-International)
    swallowing Right Alt would stop the user typing @ \\ | € etc. We detect it by
    asking the layout whether any common character needs the Ctrl+Alt shift
    state (VkKeyScanEx returns that in bits 1-2 of the high byte: 0x06).
    """
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetKeyboardLayout.restype = wintypes.HKL
        hkl = user32.GetKeyboardLayout(0)
        user32.VkKeyScanExW.restype = ctypes.c_short
        user32.VkKeyScanExW.argtypes = [ctypes.c_wchar, wintypes.HKL]
        for ch in "@\\|{}[]~€£µ²³ł€ß":
            res = user32.VkKeyScanExW(ch, hkl)
            if res == -1:
                continue
            shift_state = (res >> 8) & 0xFF
            if shift_state & 0x06 == 0x06:            # Ctrl+Alt == AltGr
                return True
    except Exception:
        log.exception("AltGr probe failed; assuming no AltGr")
    return False


class KeyboardHook:
    """Owns the hook thread and translates raw key events into dictation actions.

    `on_action(action, source)` is invoked on a worker thread, never inside the
    hook callback.
    """

    def __init__(self, machine: HotkeyStateMachine, hotkey_vk: int,
                 on_action: Callable[[Action, str], None],
                 *, escape_cancels: bool = True,
                 hotkey_modifiers: set[str] | None = None) -> None:
        self.machine = machine
        self.hotkey_vk = hotkey_vk
        # Empty set = a bare key, which is the original behaviour.
        self.hotkey_mods: frozenset[str] = frozenset(hotkey_modifiers or ())
        self.on_action = on_action
        self.escape_cancels = escape_cancels

        self._thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._thread_id: int = 0
        self._hook = None
        self._proc = None                 # must outlive the hook
        self._q: queue.Queue = queue.Queue()
        self._running = threading.Event()
        self._timer_id = 0
        self._last_event_at = 0.0
        self._combos: dict[tuple[int, frozenset], Callable[[], None]] = {}
        self._mods: set[int] = set()
        self._lock = threading.Lock()

    # -- public -----------------------------------------------------------
    def register_combo(self, vk: int, modifiers: set[str], fn: Callable[[], None]) -> None:
        """Extra chord hotkey, e.g. Ctrl+Shift+R for meeting recording."""
        self._combos[(vk, frozenset(m.lower() for m in modifiers))] = fn

    def clear_combos(self) -> None:
        self._combos.clear()

    def set_hotkey(self, vk: int, modifiers: set[str] | None = None) -> None:
        with self._lock:
            self.hotkey_vk = vk
            self.hotkey_mods = frozenset(modifiers or ())
            self.machine.reset()

    def start(self) -> bool:
        if not IS_WINDOWS:
            log.warning("keyboard hook unavailable on %s; hotkeys disabled", sys.platform)
            return False
        if self._running.is_set():
            return True
        self._running.set()
        self._worker = threading.Thread(target=self._worker_loop, name="hotkey-dispatch",
                                        daemon=True)
        self._worker.start()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._hook_loop, args=(ready,),
                                        name="hotkey-hook", daemon=True)
        self._thread.start()
        ok = ready.wait(timeout=5.0) and self._hook is not None
        if not ok:
            log.error("keyboard hook failed to install")
            self._running.clear()
        return ok

    def stop(self) -> None:
        self._running.clear()
        if IS_WINDOWS and self._thread_id:
            ctypes.WinDLL("user32").PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._q.put(None)
        for t in (self._thread, self._worker):
            if t and t.is_alive():
                t.join(timeout=2.0)
        self._thread = self._worker = None

    def reinstall(self) -> bool:
        """Re-arm after Windows drops the hook (it does so silently on timeout)."""
        log.info("reinstalling keyboard hook")
        self.stop()
        self.machine.reset()
        return self.start()

    @property
    def alive(self) -> bool:
        return bool(self._running.is_set() and self._thread and self._thread.is_alive())

    @property
    def seconds_since_last_event(self) -> float:
        return time.monotonic() - self._last_event_at if self._last_event_at else 0.0

    # -- internals ---------------------------------------------------------
    def _worker_loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            action, source = item
            try:
                self.on_action(action, source)
            except Exception:
                log.exception("hotkey action handler raised")

    def _emit(self, action: Action, source: str) -> None:
        if action is not Action.NONE:
            self._q.put((action, source))

    def _track_modifier(self, vk: int, down: bool) -> None:
        name = {VK_LSHIFT: "shift", VK_RSHIFT: "shift", VK_SHIFT: "shift",
                VK_LCONTROL: "ctrl", VK_RCONTROL: "ctrl", VK_CONTROL: "ctrl",
                VK_LMENU: "alt", VK_RMENU: "alt", VK_MENU: "alt",
                VK_LWIN: "win", VK_RWIN: "win"}.get(vk)
        if not name:
            return
        if down:
            self._mods.add(name)
            return
        self._mods.discard(name)
        # Letting go of Ctrl in a Ctrl+D hold should end the dictation, the same
        # as letting go of D. Hands-free is a toggle, so it is left running.
        if (name in self.hotkey_mods
                and self.machine.state.name in ("PENDING", "HOLDING")):
            d = self.machine.force_stop()
            self._emit(d.action, "modifier-release")

    def _handle(self, vk: int, is_down: bool, now_ms: int) -> bool:
        """Returns True to suppress the key event. Must stay fast."""
        self._last_event_at = time.monotonic()
        self._track_modifier(vk, is_down)

        with self._lock:
            hotkey_vk = self.hotkey_vk

        if vk == hotkey_vk:
            if is_down and not self.hotkey_mods.issubset(self._mods):
                # The combination is not held, so this is an ordinary keypress.
                # Pass it through untouched - binding Ctrl+D must not stop D
                # typing a letter.
                return False
            if not is_down and not self.machine.recording \
                    and self.machine.state.name == "IDLE":
                return False
            d = self.machine.key_down(now_ms) if is_down else self.machine.key_up(now_ms)
            self._emit(d.action, "hotkey")
            if is_down and self.machine.state.name == "PENDING":
                self._arm_timer()
            return d.suppress

        if is_down and vk == VK_ESCAPE and self.escape_cancels:
            d = self.machine.escape(now_ms)
            self._emit(d.action, "escape")
            return d.suppress

        if is_down and self._combos:
            for (cvk, cmods), fn in self._combos.items():
                # An empty modifier set means a bare key (Quill on Right Ctrl).
                if cvk == vk and cmods.issubset(self._mods):
                    # Dispatch off the hook thread: the callback must return fast
                    # or Windows drops the hook.
                    threading.Thread(target=fn, name="combo-action", daemon=True).start()
                    return True
        return False

    def _arm_timer(self) -> None:
        if not IS_WINDOWS or self._timer_id:
            return
        user32 = ctypes.WinDLL("user32")
        self._timer_id = user32.SetTimer(None, 0, TICK_MS, None)

    def _disarm_timer(self) -> None:
        if IS_WINDOWS and self._timer_id:
            ctypes.WinDLL("user32").KillTimer(None, self._timer_id)
            self._timer_id = 0

    def _hook_loop(self, ready: threading.Event) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE,
                                             wintypes.DWORD]
        user32.CallNextHookEx.restype = LRESULT
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM,
                                          wintypes.LPARAM]

        def callback(nCode, wParam, lParam):
            try:
                if nCode == HC_ACTION:
                    kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if not (kb.flags & LLKHF_INJECTED):      # ignore our own SendInput
                        is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                        is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)
                        if is_down or is_up:
                            if self._handle(kb.vkCode, is_down, _now_ms()):
                                return 1
            except Exception:
                log.exception("hook callback error")       # never let it propagate into Win32
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._proc = HOOKPROC(callback)                     # keep a strong reference
        self._thread_id = kernel32.GetCurrentThreadId()
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            log.error("SetWindowsHookExW failed: %s", ctypes.get_last_error())
            ready.set()
            return
        log.info("keyboard hook installed (thread %s)", self._thread_id)
        ready.set()

        msg = wintypes.MSG()
        while self._running.is_set():
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            if msg.message == WM_TIMER:
                d = self.machine.tick(_now_ms())
                self._emit(d.action, "hold")
                if self.machine.state.name != "PENDING":
                    self._disarm_timer()
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        self._disarm_timer()
        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        log.info("keyboard hook removed")


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
