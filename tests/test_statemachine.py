"""Gesture logic. Runs on any OS - no Win32 involved."""

from muesli_win.winput.statemachine import Action, HotkeyStateMachine, State


def sm(**kw):
    return HotkeyStateMachine(threshold_ms=250, double_tap_ms=400, **kw)


def test_hold_starts_after_threshold_and_stops_on_release():
    m = sm()
    d = m.key_down(1000)
    assert d.action is Action.NONE and d.wake_at_ms == 1250
    assert m.tick(1100).action is Action.NONE          # too early
    assert m.tick(1250).action is Action.START_HOLD
    assert m.state is State.HOLDING
    assert m.key_up(3000).action is Action.STOP
    assert m.state is State.IDLE


def test_short_tap_alone_does_nothing():
    m = sm()
    m.key_down(1000)
    assert m.key_up(1100).action is Action.NONE        # below threshold, single tap
    assert m.state is State.IDLE


def test_double_tap_enters_handsfree_and_single_tap_stops_it():
    m = sm()
    m.key_down(1000); m.key_up(1080)                   # tap 1
    d = m.key_down(1200)
    assert d.action is Action.NONE
    d = m.key_up(1260)                                 # tap 2, inside 400ms
    assert d.action is Action.START_HANDSFREE
    assert m.state is State.HANDSFREE and m.recording

    m.key_down(9000)                                   # single tap stops
    assert m.key_up(9060).action is Action.STOP
    assert m.state is State.IDLE


def test_taps_outside_window_do_not_toggle():
    m = sm()
    m.key_down(1000); m.key_up(1080)
    m.key_down(2000)
    assert m.key_up(2080).action is Action.NONE        # 920ms apart
    assert m.state is State.IDLE


def test_hold_is_not_counted_as_a_tap():
    """A hold then an immediate tap must not be read as a double-tap."""
    m = sm()
    m.key_down(1000); m.tick(1250); m.key_up(2000)     # a hold
    m.key_down(2100)
    assert m.key_up(2150).action is Action.NONE        # tap right after must be inert
    assert m.state is State.IDLE


def test_key_autorepeat_does_not_restart():
    m = sm()
    m.key_down(1000)
    m.tick(1250)
    for t in range(1300, 1600, 30):                    # Windows repeats keydown
        assert m.key_down(t).action is Action.NONE
    assert m.state is State.HOLDING
    assert m.key_up(1700).action is Action.STOP


def test_double_tap_disabled():
    m = sm(double_tap_enabled=False)
    m.key_down(1000); m.key_up(1080)
    m.key_down(1200)
    assert m.key_up(1260).action is Action.NONE
    assert m.state is State.IDLE


def test_escape_cancels_only_while_recording():
    m = sm()
    assert m.escape(500).action is Action.NONE
    m.key_down(1000); m.tick(1250)
    d = m.escape(1400)
    assert d.action is Action.CANCEL and d.suppress
    assert m.state is State.IDLE
    # the still-held key releasing afterwards must not emit a second STOP
    assert m.key_up(1500).action is Action.NONE


def test_force_stop_is_idempotent():
    m = sm()
    m.key_down(1000); m.tick(1250)
    assert m.force_stop().action is Action.STOP
    assert m.force_stop().action is Action.NONE


def test_suppression_flag_follows_config():
    assert sm(suppress_key=True).key_down(1).suppress is True
    assert sm(suppress_key=False).key_down(1).suppress is False


def test_handsfree_survives_autorepeat_before_stop():
    m = sm()
    m.key_down(1000); m.key_up(1080); m.key_down(1200); m.key_up(1260)
    assert m.state is State.HANDSFREE
    m.key_down(5000)
    m.key_down(5030)                                   # repeat while held
    assert m.state is State.PENDING_STOP
    assert m.key_up(5100).action is Action.STOP
