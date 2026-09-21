"""Combination hotkeys: Ctrl+Alt+D, Win+Space and so on.

The property that matters most is pass-through: binding Ctrl+D must not stop D
typing a letter. Testing KeyboardHook._handle directly works on any OS - it
touches no Win32 except a timer that no-ops off Windows.
"""
from muesli_win.winput.hook import (
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    KeyboardHook,
)
from muesli_win.winput.statemachine import Action, HotkeyStateMachine

VK_D = 0x44
VK_SPACE = 0x20


def make(vk=VK_D, mods=None, threshold=250):
    actions = []
    hook = KeyboardHook(
        HotkeyStateMachine(threshold_ms=threshold),
        vk,
        lambda a, s: actions.append((a, s)),
        hotkey_modifiers=mods,
    )
    hook._emit = lambda action, source: (
        actions.append((action, source)) if action is not Action.NONE else None)
    return hook, actions


def test_bare_key_is_passed_through_when_the_combo_is_not_held():
    """Binding Ctrl+D must leave a plain D alone."""
    hook, actions = make(mods={"ctrl"})
    assert hook._handle(VK_D, True, 1000) is False       # not suppressed
    assert actions == []


def test_combo_engages_when_the_modifier_is_held():
    hook, actions = make(mods={"ctrl"})
    hook._handle(VK_LCONTROL, True, 1000)
    assert hook._handle(VK_D, True, 1010) is True        # suppressed, it is ours
    assert hook.machine.state.name == "PENDING"
    hook.machine.tick(1300)
    assert hook.machine.state.name == "HOLDING"


def test_releasing_the_modifier_stops_a_hold():
    hook, actions = make(mods={"ctrl"})
    hook._handle(VK_LCONTROL, True, 1000)
    hook._handle(VK_D, True, 1010)
    hook._emit(hook.machine.tick(1300).action, "hold")
    assert hook.machine.recording
    hook._handle(VK_LCONTROL, False, 2000)               # let go of Ctrl first
    assert not hook.machine.recording
    assert (Action.STOP, "modifier-release") in actions


def test_multi_modifier_combo_needs_every_modifier():
    hook, _ = make(vk=VK_SPACE, mods={"ctrl", "shift"})
    hook._handle(VK_LCONTROL, True, 1000)
    assert hook._handle(VK_SPACE, True, 1005) is False   # shift missing
    hook._handle(VK_LSHIFT, True, 1010)
    assert hook._handle(VK_SPACE, True, 1015) is True    # now complete


def test_extra_modifiers_do_not_block_the_combo():
    """Ctrl+D should still fire if Shift happens to be down too."""
    hook, _ = make(mods={"ctrl"})
    hook._handle(VK_LSHIFT, True, 1000)
    hook._handle(VK_LCONTROL, True, 1005)
    assert hook._handle(VK_D, True, 1010) is True


def test_win_space_combination():
    hook, _ = make(vk=VK_SPACE, mods={"win"})
    assert hook._handle(VK_SPACE, True, 1000) is False   # Win not held
    hook._handle(VK_LWIN, True, 1005)
    assert hook._handle(VK_SPACE, True, 1010) is True


def test_bare_key_binding_still_works_unchanged():
    """No modifiers configured = the original single-key behaviour."""
    hook, _ = make(vk=0xA5, mods=None)                   # Right Alt
    assert hook._handle(0xA5, True, 1000) is True
    assert hook.machine.state.name == "PENDING"


def test_keyup_of_an_unrelated_press_is_not_swallowed():
    """A key released while idle must reach the application."""
    hook, _ = make(mods={"ctrl"})
    assert hook._handle(VK_D, False, 1000) is False


def test_alt_as_a_modifier_is_tracked():
    hook, _ = make(mods={"alt"})
    hook._handle(VK_LMENU, True, 1000)
    assert hook._handle(VK_D, True, 1005) is True


def test_set_hotkey_replaces_key_and_modifiers():
    hook, _ = make(mods={"ctrl"})
    hook.set_hotkey(VK_SPACE, {"win"})
    assert hook.hotkey_vk == VK_SPACE
    assert hook.hotkey_mods == frozenset({"win"})
    assert hook._handle(VK_D, True, 1000) is False       # old binding is gone
