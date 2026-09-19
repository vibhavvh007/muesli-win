"""SQLite store (WAL), matching Muesli's local-first model.

One connection per thread; WAL so the UI can read while a meeting writes.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .. import paths

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS dictations (
    id            TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    raw_text      TEXT NOT NULL DEFAULT '',
    text          TEXT NOT NULL DEFAULT '',
    backend       TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    language      TEXT NOT NULL DEFAULT '',
    app_name      TEXT NOT NULL DEFAULT '',
    window_title  TEXT NOT NULL DEFAULT '',
    post_processed INTEGER NOT NULL DEFAULT 0,
    word_count    INTEGER NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dictations_created ON dictations(created_at DESC);

CREATE TABLE IF NOT EXISTS meetings (
    id            TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    ended_at      REAL,
    title         TEXT NOT NULL DEFAULT '',
    transcript    TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    summary_model TEXT NOT NULL DEFAULT '',
    template_id   TEXT NOT NULL DEFAULT '',
    audio_path    TEXT NOT NULL DEFAULT '',
    duration_ms   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_meetings_created ON meetings(created_at DESC);

CREATE TABLE IF NOT EXISTS meeting_segments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start_ms    INTEGER NOT NULL,
    end_ms      INTEGER NOT NULL,
    speaker     TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',   -- 'you' | 'others'
    text        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_segments_meeting ON meeting_segments(meeting_id, start_ms);

CREATE TABLE IF NOT EXISTS stats (
    day          TEXT PRIMARY KEY,
    words        INTEGER NOT NULL DEFAULT 0,
    dictations   INTEGER NOT NULL DEFAULT 0,
    seconds      REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

SCHEMA_VERSION = 1


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or paths.db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        with self._init_lock:
            conn = self._conn()
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=15000")
            self._local.conn = conn
        return conn

    # -- dictations --------------------------------------------------------
    def add_dictation(self, **kw: Any) -> str:
        did = kw.pop("id", None) or uuid.uuid4().hex
        row = {
            "id": did, "created_at": kw.pop("created_at", time.time()),
            "duration_ms": int(kw.pop("duration_ms", 0)),
            "raw_text": kw.pop("raw_text", ""), "text": kw.pop("text", ""),
            "backend": kw.pop("backend", ""), "model": kw.pop("model", ""),
            "language": kw.pop("language", ""), "app_name": kw.pop("app_name", ""),
            "window_title": kw.pop("window_title", ""),
            "post_processed": int(bool(kw.pop("post_processed", False))),
            "latency_ms": int(kw.pop("latency_ms", 0)),
        }
        row["word_count"] = len(row["text"].split())
        cols = ",".join(row)
        self._conn().execute(
            f"INSERT INTO dictations({cols}) VALUES({','.join('?' * len(row))})",
            tuple(row.values()),
        )
        self._bump_stats(row["word_count"], row["duration_ms"] / 1000.0)
        return did

    def list_dictations(self, limit: int = 50, offset: int = 0) -> list[dict]:
        cur = self._conn().execute(
            "SELECT * FROM dictations ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_dictation(self, did: str) -> dict | None:
        cur = self._conn().execute("SELECT * FROM dictations WHERE id=?", (did,))
        row = cur.fetchone()
        return dict(row) if row else None

    def last_dictation(self) -> dict | None:
        cur = self._conn().execute("SELECT * FROM dictations ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else None

    def delete_dictation(self, did: str) -> None:
        self._conn().execute("DELETE FROM dictations WHERE id=?", (did,))

    # -- meetings ----------------------------------------------------------
    def create_meeting(self, title: str = "") -> str:
        mid = uuid.uuid4().hex
        self._conn().execute(
            "INSERT INTO meetings(id, created_at, title) VALUES(?,?,?)",
            (mid, time.time(), title),
        )
        return mid

    def add_segment(self, meeting_id: str, start_ms: int, end_ms: int,
                    text: str, source: str = "", speaker: str = "") -> None:
        self._conn().execute(
            "INSERT INTO meeting_segments(meeting_id,start_ms,end_ms,speaker,source,text)"
            " VALUES(?,?,?,?,?,?)",
            (meeting_id, int(start_ms), int(end_ms), speaker, source, text),
        )

    def segments(self, meeting_id: str) -> list[dict]:
        cur = self._conn().execute(
            "SELECT * FROM meeting_segments WHERE meeting_id=? ORDER BY start_ms", (meeting_id,)
        )
        return [dict(r) for r in cur.fetchall()]

    def finish_meeting(self, meeting_id: str, *, transcript: str = "", duration_ms: int = 0,
                       audio_path: str = "", title: str = "") -> None:
        sets, args = ["ended_at=?", "transcript=?", "duration_ms=?", "audio_path=?"], \
                     [time.time(), transcript, int(duration_ms), audio_path]
        if title:
            sets.append("title=?")
            args.append(title)
        args.append(meeting_id)
        self._conn().execute(f"UPDATE meetings SET {','.join(sets)} WHERE id=?", tuple(args))

    def set_meeting_notes(self, meeting_id: str, notes: str, model: str = "",
                          template_id: str = "") -> None:
        self._conn().execute(
            "UPDATE meetings SET notes=?, summary_model=?, template_id=? WHERE id=?",
            (notes, model, template_id, meeting_id),
        )

    def set_meeting_title(self, meeting_id: str, title: str) -> None:
        self._conn().execute("UPDATE meetings SET title=? WHERE id=?", (title, meeting_id))

    def get_meeting(self, mid: str) -> dict | None:
        cur = self._conn().execute("SELECT * FROM meetings WHERE id=?", (mid,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_meetings(self, limit: int = 50, offset: int = 0) -> list[dict]:
        cur = self._conn().execute(
            "SELECT id,created_at,ended_at,title,duration_ms,notes<>'' AS has_notes"
            " FROM meetings ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        return [dict(r) for r in cur.fetchall()]

    def last_meeting(self) -> dict | None:
        cur = self._conn().execute("SELECT * FROM meetings ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else None

    def delete_meeting(self, mid: str) -> None:
        self._conn().execute("DELETE FROM meetings WHERE id=?", (mid,))

    # -- stats -------------------------------------------------------------
    def _bump_stats(self, words: int, seconds: float) -> None:
        day = time.strftime("%Y-%m-%d")
        self._conn().execute(
            "INSERT INTO stats(day,words,dictations,seconds) VALUES(?,?,1,?) "
            "ON CONFLICT(day) DO UPDATE SET words=words+excluded.words, "
            "dictations=dictations+1, seconds=seconds+excluded.seconds",
            (day, words, seconds),
        )

    def stats(self, days: int = 30) -> list[dict]:
        cur = self._conn().execute(
            "SELECT * FROM stats ORDER BY day DESC LIMIT ?", (days,)
        )
        return [dict(r) for r in cur.fetchall()]

    def totals(self) -> dict:
        cur = self._conn().execute(
            "SELECT COALESCE(SUM(words),0) w, COALESCE(SUM(dictations),0) d,"
            " COALESCE(SUM(seconds),0) s FROM stats"
        )
        r = cur.fetchone()
        return {"words": r["w"], "dictations": r["d"], "seconds": r["s"]}

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
