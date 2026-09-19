"""Hotkey state machine — pure logic, no Win32, so it can be tested anywhere.

Reproduces Muesli's dictation gesture set:
  hold           press and keep held past `threshold_ms`  -> record while held
  double-tap     two taps inside `double_tap_ms`          -> hands-free, runs until stopped
  tap (in h/f)   one press while hands-free               -> stop
  Esc            while recording                          -> cancel and discard

Time is injected (`now_ms`) so tests are deterministic and there is no sleeping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class State(Enum):
    IDLE = auto()
    PENDING = auto()        # key down, not yet past the hold threshold
    HOLDING = auto()        # recording, ends on key release
    HANDSFREE = auto()      # recording, ends on next tap
    PENDING_STOP = auto()   # key down while hands-free


class Action(Enum):
    NONE = auto()
    START_HOLD = auto()
    START_HANDSFREE = auto()
    STOP = auto()
    CANCEL = auto()


@dataclass
class Decision:
    action: Action = Action.NONE
    suppress: bool = False          # hide this key event from the rest of the system
    wake_at_ms: int | None = None   # ask the driver to call tick() at this time


@dataclass
class HotkeyStateMachine:
    threshold_ms: int = 250
    double_tap_ms: int = 400
    double_tap_enabled: bool = True
    suppress_key: bool = True

    state: State = State.IDLE
    _down_at: int = 0
    _last_tap_at: int = -10_000
    _pending_tap_deadline: int | None = field(default=None)

    # -- queries -----------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self.state in (State.HOLDING, State.HANDSFREE, State.PENDING_STOP)

    # -- events ------------------------------------------------------------
    def key_down(self, now_ms: int) -> Decision:
        sup = self.suppress_key
        if self.state in (State.HOLDING, State.PENDING):
            return Decision(suppress=sup)                    # auto-repeat, ignore
        if self.state == State.HANDSFREE:
            self.state = State.PENDING_STOP
            return Decision(suppress=sup)
        if self.state == State.PENDING_STOP:
            return Decision(suppress=sup)
        # IDLE
        self.state = State.PENDING
        self._down_at = now_ms
        return Decision(suppress=sup, wake_at_ms=now_ms + self.threshold_ms)

    def key_up(self, now_ms: int) -> Decision:
        sup = self.suppress_key
        if self.state == State.HOLDING:
            self.state = State.IDLE
            self._last_tap_at = -10_000                      # a hold is not a tap
            return Decision(action=Action.STOP, suppress=sup)

        if self.state == State.PENDING_STOP:
            self.state = State.IDLE
            self._last_tap_at = -10_000
            return Decision(action=Action.STOP, suppress=sup)

        if self.state == State.PENDING:
            # released before the threshold -> a tap
            self.state = State.IDLE
            if self.double_tap_enabled and (now_ms - self._last_tap_at) <= self.double_tap_ms:
                self._last_tap_at = -10_000
                self.state = State.HANDSFREE
                return Decision(action=Action.START_HANDSFREE, suppress=sup)
            self._last_tap_at = now_ms
            return Decision(suppress=sup)

        return Decision(suppress=sup)

    def tick(self, now_ms: int) -> Decision:
        """Driver calls this when a scheduled wake time arrives (or on a poll)."""
        if self.state == State.PENDING and (now_ms - self._down_at) >= self.threshold_ms:
            self.state = State.HOLDING
            return Decision(action=Action.START_HOLD)
        return Decision()

    def escape(self, now_ms: int) -> Decision:
        """Esc while recording discards the take."""
        if self.recording:
            self.state = State.IDLE
            self._last_tap_at = -10_000
            return Decision(action=Action.CANCEL, suppress=True)
        return Decision()

    def force_stop(self) -> Decision:
        """External stop: idle timeout, max duration, device loss, tray click."""
        if self.recording:
            self.state = State.IDLE
            return Decision(action=Action.STOP)
        return Decision()

    def reset(self) -> None:
        self.state = State.IDLE
        self._down_at = 0
        self._last_tap_at = -10_000
