"""Mute other apps while dictating.

Muesli mutes system audio during dictation but deliberately does NOT pause
media, so a video keeps playing silently and you do not lose your place. Same
behaviour here, via the Windows per-session volume API (pycaw): we mute every
audio session except our own and restore the previous state afterwards.
"""
from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"


class SystemAudioMuter:
    def __init__(self) -> None:
        self._saved: list[tuple[object, bool]] = []
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def mute(self) -> None:
        if not IS_WINDOWS or self._active:
            return
        try:
            import comtypes
            from pycaw.pycaw import AudioUtilities
        except ImportError:
            log.info("pycaw not installed; system audio will not be muted")
            return
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            own_pid = os.getpid()
            self._saved = []
            for session in AudioUtilities.GetAllSessions():
                try:
                    if session.Process and session.Process.pid == own_pid:
                        continue                      # never mute our own cues
                    vol = session.SimpleAudioVolume
                    if vol is None:
                        continue
                    self._saved.append((vol, bool(vol.GetMute())))
                    vol.SetMute(1, None)
                except Exception:
                    continue                          # a session can vanish mid-loop
            self._active = True
        except Exception:
            log.exception("could not mute system audio")

    def restore(self) -> None:
        if not self._active:
            return
        for vol, was_muted in self._saved:
            try:
                vol.SetMute(1 if was_muted else 0, None)
            except Exception:
                continue
        self._saved = []
        self._active = False


class NullMuter:
    active = False

    def mute(self) -> None: ...
    def restore(self) -> None: ...


def make_muter(enabled: bool):
    return SystemAudioMuter() if (enabled and IS_WINDOWS) else NullMuter()
