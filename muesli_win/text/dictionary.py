"""Personal dictionary with fuzzy matching.

Muesli's single biggest accuracy win for work vocabulary is the custom word
list, matched with Jaro-Winkler rather than exact equality - so "you'll trahuman",
"ultra human" and "ultrahuman" all correct to the same thing.

Config format is Muesli's, unchanged:
    {"word": "ultrahuman", "replacement": "Ultrahuman", "matching_threshold": 0.85}

`word` may be a multi-word phrase; phrases are matched over an n-gram window.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\w+(?:'\w+)?|\s+|[^\w\s]", re.UNICODE)


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 0.0
    window = max(la, lb) // 2 - 1
    if window < 0:
        window = 0
    a_flags = [False] * la
    b_flags = [False] * lb
    matches = 0
    for i, ca in enumerate(a):
        lo = max(0, i - window)
        hi = min(i + window + 1, lb)
        for j in range(lo, hi):
            if not b_flags[j] and b[j] == ca:
                a_flags[i] = b_flags[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(la):
        if a_flags[i]:
            while not b_flags[k]:
                k += 1
            if a[i] != b[k]:
                transpositions += 1
            k += 1
    t = transpositions / 2
    m = float(matches)
    return (m / la + m / lb + (m - t) / m) / 3.0


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    """Jaro, boosted for a shared prefix - the same metric Muesli uses."""
    j = jaro(a, b)
    if j < 0.7:
        return j
    prefix = 0
    for ca, cb in zip(a[:4], b[:4], strict=False):   # shortest prefix wins
        if ca != cb:
            break
        prefix += 1
    return j + prefix * prefix_scale * (1 - j)


@dataclass
class Entry:
    word: str
    replacement: str
    threshold: float = 0.85

    @property
    def n_words(self) -> int:
        return len(self.word.split())


# STT routinely splits one spoken word into several tokens ("ultra human"), so a
# one-word entry still has to be tested against multi-token spans.
MAX_SPAN = 4
# Character similarity alone is not enough: "ultra" scores well against
# "ultrahuman", and so does "ultrahuman rocks". Both are wrong. A candidate is
# only considered when its length (ignoring spaces) sits close to the entry's,
# which rejects both the truncation and the over-reach.
LEN_LO, LEN_HI = 0.78, 1.28


def _squash(s: str) -> str:
    return "".join(s.lower().split())


class Dictionary:
    def __init__(self, entries: list[dict] | None = None) -> None:
        self.entries: list[Entry] = []
        for e in entries or []:
            try:
                word = str(e.get("word", "")).strip()
                if not word:
                    continue
                self.entries.append(Entry(
                    word=word,
                    replacement=str(e.get("replacement", word)),
                    threshold=float(e.get("matching_threshold", 0.85)),
                ))
            except (TypeError, ValueError):
                log.warning("skipping malformed dictionary entry: %r", e)
        self._squashed = [_squash(e.word) for e in self.entries]
        self.max_span = min(MAX_SPAN, max((e.n_words for e in self.entries), default=1) + 2)

    def __len__(self) -> int:
        return len(self.entries)

    def _best_match(self, phrase: str) -> tuple[Entry | None, float]:
        cand = _squash(phrase)
        if not cand:
            return None, 0.0
        best, score = None, 0.0
        for entry, target in zip(self.entries, self._squashed, strict=True):
            if not target:
                continue
            ratio = len(cand) / len(target)
            if not (LEN_LO <= ratio <= LEN_HI):
                continue
            s_ = jaro_winkler(cand, target)
            if s_ >= entry.threshold and s_ > score:
                best, score = entry, s_
        return best, score

    def apply(self, text: str) -> str:
        if not text or not self.entries:
            return text
        tokens = _TOKEN_RE.findall(text)
        word_idx = [i for i, t in enumerate(tokens) if t.strip() and t[0].isalnum()]
        if not word_idx:
            return text

        p = 0
        while p < len(word_idx):
            matched = False
            # Longest span first so "ring air" beats a stray single-word match.
            for span in range(min(self.max_span, len(word_idx) - p), 0, -1):
                idxs = word_idx[p:p + span]
                if not _contiguous(tokens, idxs):
                    continue        # a comma or full stop sits inside the span
                phrase = " ".join(tokens[i] for i in idxs)
                entry, _ = self._best_match(phrase)
                if entry is None:
                    continue
                tokens[idxs[0]] = _match_case(phrase, entry.replacement)
                for i in idxs[1:]:
                    tokens[i] = ""
                    if i - 1 >= 0 and not tokens[i - 1].strip():
                        tokens[i - 1] = ""      # drop the separator too
                p += span
                matched = True
                break
            if not matched:
                p += 1
        return re.sub(r"[ \t]{2,}", " ", "".join(tokens))


def _contiguous(tokens: list[str], idxs: list[int]) -> bool:
    """True if only whitespace separates the chosen word tokens.

    Stops a phrase entry from matching across punctuation, so "ring, air
    quality" is left alone while "ring air quality" is not.
    """
    for a, b in zip(idxs, idxs[1:], strict=False):
        if any(tokens[k].strip() for k in range(a + 1, b)):
            return False
    return True


def _match_case(original: str, replacement: str) -> str:
    """Keep the speaker's capitalisation when the entry is all-lowercase."""
    if not original or not replacement:
        return replacement
    if replacement != replacement.lower():
        return replacement                      # entry specifies its own casing
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement
