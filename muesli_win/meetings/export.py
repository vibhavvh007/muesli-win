"""Export a meeting as Markdown or PDF (paginated, US Letter - as Muesli does)."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from .. import paths

log = logging.getLogger(__name__)


def _safe_name(title: str, when: float) -> str:
    stamp = time.strftime("%Y-%m-%d_%H%M", time.localtime(when))
    slug = re.sub(r"[^\w\- ]+", "", title or "").strip().replace(" ", "_")[:60]
    return f"Muesli_{stamp}{'_' + slug if slug else ''}"


def to_markdown(meeting: dict, segments: list[dict], content: str = "notes") -> str:
    when = time.strftime("%d %B %Y, %H:%M", time.localtime(meeting.get("created_at", 0)))
    title = meeting.get("title") or "Meeting"
    mins = int(meeting.get("duration_ms", 0) / 60000)
    out = [f"# {title}", "", f"*{when} · {mins} min*", ""]

    if content in ("notes", "both") and meeting.get("notes"):
        out += [meeting["notes"].strip(), ""]
    if content in ("transcript", "both"):
        out += ["## Transcript", ""]
        if segments:
            for s in segments:
                ts = f"{int(s['start_ms'] / 60000):02d}:{int(s['start_ms'] / 1000) % 60:02d}"
                who = s.get("speaker") or ("You" if s.get("source") == "you" else "Others")
                out.append(f"**[{ts}] {who}:** {s['text']}")
        else:
            out.append(meeting.get("transcript", "") or "_No transcript._")
        out.append("")
    return "\n".join(out).strip() + "\n"


def write_markdown(meeting: dict, segments: list[dict], content: str = "notes",
                   out_dir: Path | None = None) -> Path:
    out_dir = out_dir or paths.exports_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (_safe_name(meeting.get("title", ""),
                                 meeting.get("created_at", time.time())) + ".md")
    path.write_text(to_markdown(meeting, segments, content), encoding="utf-8")
    return path


def write_pdf(meeting: dict, segments: list[dict], content: str = "notes",
              out_dir: Path | None = None) -> Path:
    """Needs reportlab. Raises RuntimeError with a plain message if it is missing."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise RuntimeError("PDF export needs reportlab - pip install reportlab") from exc

    out_dir = out_dir or paths.exports_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (_safe_name(meeting.get("title", ""),
                                 meeting.get("created_at", time.time())) + ".pdf")

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10.5, leading=15,
                          spaceAfter=6)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=17, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12.5, spaceBefore=12,
                        spaceAfter=6)
    meta = ParagraphStyle("meta", parent=body, textColor="#666666", fontSize=9)

    doc = SimpleDocTemplate(str(path), pagesize=letter, title=meeting.get("title", "Meeting"),
                            leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.9 * inch, bottomMargin=0.9 * inch)
    flow = []
    md = to_markdown(meeting, segments, content)
    for line in md.splitlines():
        s = line.rstrip()
        if not s:
            flow.append(Spacer(1, 5))
        elif s.startswith("# "):
            flow.append(Paragraph(_esc(s[2:]), h1))
        elif s.startswith("## "):
            flow.append(Paragraph(_esc(s[3:]), h2))
        elif s.startswith("### "):
            flow.append(Paragraph(f"<b>{_esc(s[4:])}</b>", body))
        elif s.startswith("*") and s.endswith("*") and not s.startswith("**"):
            flow.append(Paragraph(_esc(s.strip("*")), meta))
        elif s.lstrip().startswith(("- ", "* ")):
            flow.append(Paragraph(f"• {_inline(s.lstrip()[2:])}", body))
        else:
            flow.append(Paragraph(_inline(s), body))
    doc.build(flow)
    return path


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(s: str) -> str:
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", s)
    s = s.replace("- [ ] ", "☐ ").replace("- [x] ", "☑ ")
    return s
