"""Post-meeting hook: run a user executable with the meeting JSON on stdin.

Same contract as Muesli's, so a hook script written for the Mac works here.
The hook is user-supplied, so it is run without a shell, with a timeout, and
its failure never affects the meeting that was just saved.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def payload(meeting: dict, segments: list[dict]) -> dict:
    return {
        "id": meeting.get("id"),
        "title": meeting.get("title", ""),
        "created_at": meeting.get("created_at"),
        "ended_at": meeting.get("ended_at"),
        "duration_ms": meeting.get("duration_ms", 0),
        "transcript": meeting.get("transcript", ""),
        "notes": meeting.get("notes", ""),
        "audio_path": meeting.get("audio_path", ""),
        "segments": [
            {"start_ms": s["start_ms"], "end_ms": s["end_ms"], "speaker": s.get("speaker", ""),
             "source": s.get("source", ""), "text": s["text"]}
            for s in segments
        ],
    }


def run(cfg, meeting: dict, segments: list[dict]) -> None:
    if not cfg.get("meeting_hook_enabled", False):
        return
    raw = str(cfg.get("meeting_hook_path", "")).strip()
    if not raw:
        return
    exe = Path(raw)
    if not exe.exists():
        log.error("meeting hook not found: %s", exe)
        return
    timeout = int(cfg.get("meeting_hook_timeout_seconds", 30))
    data = json.dumps(payload(meeting, segments), ensure_ascii=False)

    def _run() -> None:
        try:
            proc = subprocess.run(
                [str(exe)], input=data, text=True, capture_output=True,
                timeout=timeout, shell=False,
            )
            if proc.returncode != 0:
                log.warning("meeting hook exited %d: %s", proc.returncode,
                            (proc.stderr or "")[:400])
            else:
                log.info("meeting hook completed")
        except subprocess.TimeoutExpired:
            log.error("meeting hook timed out after %ds", timeout)
        except Exception:
            log.exception("meeting hook failed")

    threading.Thread(target=_run, name="meeting-hook", daemon=True).start()
