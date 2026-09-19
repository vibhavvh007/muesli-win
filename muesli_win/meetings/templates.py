"""Note templates. `auto` picks by transcript shape, as Muesli does."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Template:
    id: str
    name: str
    instructions: str


BUILTIN: dict[str, Template] = {
    "general": Template("general", "General meeting", """Write structured notes.
## Summary
Three to five sentences on what the meeting was about and what was decided.
## Key points
Bullets, grouped by topic.
## Decisions
Each decision on its own line. Write "None recorded." if there were none.
## Action items
- [ ] owner - action - due date if one was stated
## Open questions
Anything raised and left unresolved."""),

    "standup": Template("standup", "Stand-up", """Write notes per person.
## By person
For each speaker: what they did, what they are doing next, what is blocking them.
## Blockers
Consolidated list with owners.
## Action items
- [ ] owner - action"""),

    "one_on_one": Template("one_on_one", "1:1", """Write notes for a private 1:1.
## Discussed
## Feedback given and received
## Commitments
- [ ] owner - commitment - by when
## Follow up next time"""),

    "interview": Template("interview", "Interview", """Write interview notes.
## Candidate summary
## Evidence against each area probed
## Strengths
## Concerns
## Recommendation
State the recommendation and the confidence behind it."""),

    "client_call": Template("client_call", "Client / vendor call", """Write notes for a
commercial call.
## Summary
## Commercial terms discussed
Prices, volumes, dates, payment terms - quote figures exactly as stated.
## Commitments made by us
## Commitments made by them
## Risks and open points
## Action items
- [ ] owner - action - due date"""),
}


def pick(template_id: str, transcript: str) -> Template:
    if template_id in BUILTIN:
        return BUILTIN[template_id]
    if template_id != "auto":
        return BUILTIN["general"]

    low = transcript.lower()
    speakers = {ln.split(":", 1)[0] for ln in transcript.splitlines() if ":" in ln}
    if any(w in low for w in ("blocker", "blocked on", "yesterday i", "today i'll",
                              "stand up", "standup")):
        return BUILTIN["standup"]
    if any(w in low for w in ("your cv", "your resume", "tell me about yourself",
                              "walk me through your", "why do you want to work")):
        return BUILTIN["interview"]
    if any(w in low for w in ("purchase order", "invoice", "payment terms", "pricing",
                              "quotation", "contract", "discount", "incoterm")):
        return BUILTIN["client_call"]
    if len(speakers) <= 3 and any(w in low for w in ("how are you finding", "career",
                                                      "feedback for you", "your growth")):
        return BUILTIN["one_on_one"]
    return BUILTIN["general"]


def choices() -> list[tuple[str, str]]:
    return [("auto", "Auto-detect")] + [(t.id, t.name) for t in BUILTIN.values()]
