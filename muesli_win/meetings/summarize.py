"""Turn a meeting transcript into notes and a title, via the configured LLM."""
from __future__ import annotations

import logging
import re
import time

from ..text.llm import LLMError, chat, target_from_config
from . import templates

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 60_000

SYSTEM = """You write meeting notes from a transcript.

Rules:
- Use only what is in the transcript. Never invent a decision, a name, a number or a date.
- If the transcript does not cover a section, write "Not discussed." under it rather than
  guessing or omitting the heading.
- Attribute actions to the speaker who accepted them. If ownership was never stated, write
  "owner not stated".
- Quote figures, dates and commercial terms exactly as spoken.
- Output markdown only, starting at the first heading. No preamble, no code fences."""

TITLE_SYSTEM = ("Give this meeting a title of at most eight words. Output the title alone: "
                "no quotes, no punctuation at the end, no preamble.")


def _trim(transcript: str) -> str:
    """Keep the start and the end, which is where the framing and the actions live."""
    if len(transcript) <= MAX_TRANSCRIPT_CHARS:
        return transcript
    head = transcript[: MAX_TRANSCRIPT_CHARS // 2]
    tail = transcript[-MAX_TRANSCRIPT_CHARS // 2:]
    return f"{head}\n\n[... middle of the transcript omitted for length ...]\n\n{tail}"


def summarise(cfg, transcript: str, template_id: str = "auto") -> tuple[str, str]:
    """-> (markdown notes, template id used). Raises LLMError if unavailable."""
    if not transcript.strip():
        return "", ""
    template = templates.pick(template_id, transcript)
    target = target_from_config(cfg, "summary")
    user = (f"{template.instructions}\n\n--- TRANSCRIPT ---\n{_trim(transcript)}")

    retries = max(1, int(cfg.get("meeting_summary_retry_count", 3)))
    last: Exception | None = None
    for attempt in range(retries):
        try:
            out = chat(target, SYSTEM, user, temperature=0.2, timeout=240)
            notes = _strip_fences(out)
            if notes.strip():
                return notes, template.id
            last = LLMError("model returned empty notes")
        except LLMError as exc:
            last = exc
            log.warning("summary attempt %d/%d failed: %s", attempt + 1, retries, exc)
            time.sleep(min(2 ** attempt, 8))
    raise LLMError(str(last))


def make_title(cfg, transcript: str) -> str:
    try:
        target = target_from_config(cfg, "summary")
        out = chat(target, TITLE_SYSTEM, _trim(transcript)[:6000], temperature=0.3,
                   timeout=60, max_tokens=32)
        title = _strip_fences(out).strip().strip('"').splitlines()[0] if out else ""
        return title[:80]
    except Exception as exc:
        log.info("could not generate a title (%s); falling back to the date", exc)
        return ""


def _strip_fences(text: str) -> str:
    text = re.sub(r"^\s*```[\w]*\s*\n?", "", text)
    text = re.sub(r"\n?\s*```\s*$", "", text)
    return text.strip()
