"""Short start/stop/error cues.

Generated rather than shipped as files, so the installer stays small and there
is nothing to go missing. Played on a worker thread - never block a hotkey.
"""
from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"

# (frequency Hz, duration ms) pairs
CUES = {
    "start": [(880, 60)],
    "stop": [(660, 55)],
    "cancel": [(400, 70), (300, 70)],
    "error": [(300, 120), (240, 140)],
    "ready": [(700, 50), (950, 70)],
}


class Sounds:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def play(self, cue: str) -> None:
        if not self.enabled or cue not in CUES:
            return
        threading.Thread(target=self._play, args=(cue,), daemon=True).start()

    def _play(self, cue: str) -> None:
        try:
            if IS_WINDOWS:
                import winsound
                for freq, ms in CUES[cue]:
                    winsound.Beep(freq, ms)
            else:
                log.debug("sound cue: %s", cue)
        except Exception:
            log.debug("could not play cue %s", cue, exc_info=True)
