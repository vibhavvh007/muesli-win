"""Deterministic transcript cleanup, applied before any LLM sees the text.

Order matters and is deliberate:
  1. dictionary       - fix vocabulary first, so later rules see real words
  2. voice commands   - "scratch that", "new paragraph", spoken punctuation
  3. filler removal   - um / uh / er, and only when clearly filler
  4. tidy             - whitespace, capitalisation, spacing around punctuation

Everything here is reversible in config and none of it rewrites meaning. The
optional LLM pass runs after, and is instructed not to paraphrase.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Standalone filler only. "um" inside a word, or "like" used as a real verb
# ("I like it"), must survive - so `like` is deliberately NOT in this list.
FILLERS = ("um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "mmm", "mhm")
_FILLER_RE = re.compile(
    r"(?<![\w'])(?:" + "|".join(FILLERS) + r")(?![\w'])[ \t]*,?", re.IGNORECASE
)

# "scratch that" / "delete that" removes the preceding sentence or clause.
_SCRATCH_RE = re.compile(
    r"[\s,]*\b(?:scratch|strike|delete|ignore)\s+that\b[\s,.!?]*", re.IGNORECASE
)

SPOKEN_PUNCT = {
    "full stop": ".", "period": ".", "comma": ",", "question mark": "?",
    "exclamation mark": "!", "exclamation point": "!", "colon": ":",
    "semicolon": ";", "open paren": "(", "close paren": ")",
    "open parenthesis": "(", "close parenthesis": ")", "hyphen": "-",
    "dash": "-", "ellipsis": "...",
}
_NEWLINE_CMDS = {
    "new paragraph": "\n\n", "new line": "\n", "newline": "\n", "next line": "\n",
}


def strip_fillers(text: str) -> str:
    out = _FILLER_RE.sub(" ", text)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def apply_scratch_that(text: str) -> str:
    """Delete the sentence preceding each 'scratch that'.

    The sentence being scratched ends in the terminator immediately before the
    command, so that terminator has to be discarded before looking for the
    boundary - otherwise the very sentence the speaker retracted is the one
    that survives.

    Only sentence enders and line breaks count as boundaries. Commas do not: a
    speaker correcting themselves mid-list expects the whole sentence to go,
    and guessing at clause edges makes the behaviour unpredictable.
    """
    guard = 0
    while guard < 50:
        guard += 1
        m = _SCRATCH_RE.search(text)
        if not m:
            return text
        head = text[: m.start()].rstrip()
        head_wo_terminator = head.rstrip(".?!\n ")          # drop the scratched sentence's end
        boundary = max(head_wo_terminator.rfind("."), head_wo_terminator.rfind("?"),
                       head_wo_terminator.rfind("!"), head_wo_terminator.rfind("\n"))
        keep = head[: boundary + 1] if boundary >= 0 else ""
        text = (keep + " " + text[m.end():]).strip()
    return text


def apply_voice_commands(text: str) -> str:
    for phrase, repl in _NEWLINE_CMDS.items():
        text = re.sub(rf"(?<![\w']){re.escape(phrase)}(?![\w'])", repl, text,
                      flags=re.IGNORECASE)
    for phrase, mark in SPOKEN_PUNCT.items():
        text = re.sub(rf"(?<![\w'])\s*{re.escape(phrase)}(?![\w'])", mark, text,
                      flags=re.IGNORECASE)
    return text


def tidy(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)          # no space before punctuation
    text = re.sub(r"([,;:])(?=\S)", r"\1 ", text)         # one space after
    text = re.sub(r"([.!?])(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"[ \t]+\n", "\n", text)              # no space before a break
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if text:
        text = text[0].upper() + text[1:]
    # capitalise after a sentence ender or a line break
    text = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    text = re.sub(r"(\n+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text


def clean(text: str, *, dictionary=None, strip_filler: bool = True,
          scratch_that: bool = True, voice_commands: bool = True) -> str:
    """The full deterministic pass."""
    if not text:
        return ""
    if dictionary is not None and len(dictionary):
        text = dictionary.apply(text)
    if voice_commands:
        text = apply_voice_commands(text)
    if scratch_that:
        text = apply_scratch_that(text)
    if strip_filler:
        text = strip_fillers(text)
    return tidy(text)


# --- guard rails for the optional LLM pass ---------------------------------
_FENCE_RE = re.compile(r"^\s*```[\w]*\s*|\s*```\s*$")
_LABEL_RE = re.compile(
    r"^\s*(?:cleaned(?:\s+(?:up|text|transcript))?|output|result|here(?:'s| is)"
    r"(?:\s+the)?(?:\s+\w+)*)\s*[:\-]\s*", re.IGNORECASE
)


def sanitise_llm_output(raw: str, original: str, *, max_growth: float = 1.6,
                        dedupe: bool = True) -> str:
    """Accept the model's cleanup only if it plausibly *is* the cleanup.

    Small local models wrap output in fences, prefix it with "Cleaned text:",
    answer the transcript instead of cleaning it, or repeat themselves. Any of
    those would put junk in the user's document, so a suspect result is
    discarded and the deterministic text is kept.
    """
    if not raw:
        return original
    out = raw.strip()
    out = _FENCE_RE.sub("", out).strip()
    out = _LABEL_RE.sub("", out).strip()
    if out.startswith(("<", "[")) and original and not original.startswith(("<", "[")):
        return original
    out = out.strip('"').strip()
    if not out:
        return original
    # A model that answered twice must be halved before the length guards run,
    # or a legitimate cleanup gets thrown away for being "too long". Off for
    # rewriting, where deliberately repetitive output is a valid answer.
    if dedupe:
        half = len(out) // 2
        if half > 20 and out[:half].strip().rstrip(".") == out[half:].strip().rstrip("."):
            out = out[:half].strip()
    # length sanity: cleanup removes words, it does not invent paragraphs
    if len(out) > max(40, len(original) * max_growth):
        log.warning("post-processor output too long (%d vs %d); keeping original",
                    len(out), len(original))
        return original
    if len(out) < len(original) * 0.35 and len(original) > 40:
        log.warning("post-processor dropped too much; keeping original")
        return original
    return out
