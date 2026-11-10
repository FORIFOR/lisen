""""今日の成長": moments where something the learner used to miss is understood in new audio."""
from __future__ import annotations

from . import db


def add(conn, kind: str, ref: str, title: str, detail: str = "") -> None:
    """One event per (kind, ref) per day."""
    today = db.local_date()
    if conn.execute("SELECT 1 FROM growth_events WHERE kind=? AND ref=? AND date=?", (kind, ref, today)).fetchone():
        return
    conn.execute("INSERT INTO growth_events (created_at, date, kind, ref, title, detail) VALUES (?,?,?,?,?,?)",
                 (db.now_iso(), today, kind, ref, title, detail))


def recent(days: int = 7, limit: int = 8) -> list[dict]:
    from datetime import date, timedelta
    since = (date.fromisoformat(db.local_date()) - timedelta(days=days - 1)).isoformat()
    return [dict(r) for r in db.q("SELECT * FROM growth_events WHERE date>=? ORDER BY id DESC LIMIT ?", (since, limit))]
