"""Spaced repetition with FSRS (py-fsrs, the reference Python implementation of the same
FSRS algorithm that ts-fsrs implements). Scheduling runs on the server so every device
sees the same review queue."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fsrs import Card, Rating, Scheduler

from . import db

_scheduler = Scheduler(desired_retention=0.9, enable_fuzzing=False)

RATING_LABEL = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


def rating_from_accuracy(acc: float) -> Rating:
    if acc >= 0.999:
        return Rating.Good
    if acc >= 0.85:
        return Rating.Hard
    return Rating.Again


def card_id(kind: str, ref: str) -> str:
    return f"{kind}:{ref}"


def get(kind: str, ref: str):
    return db.q1("SELECT * FROM srs_cards WHERE id=?", (card_id(kind, ref),))


def review(conn, kind: str, ref: str, rating: Rating, when: datetime | None = None, create: bool = True) -> dict | None:
    """Apply a review. Creates the card if it doesn't exist (only when create=True)."""
    when = when or db.now()
    cid = card_id(kind, ref)
    row = conn.execute("SELECT * FROM srs_cards WHERE id=?", (cid,)).fetchone()
    if row is None:
        if not create:
            return None
        card = Card()
        card, _ = _scheduler.review_card(card, rating, when)
        conn.execute(
            "INSERT INTO srs_cards (id, kind, ref, card, due, created_at, reps, lapses, last_rating) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, kind, ref, json.dumps(card.to_dict()), card.due.isoformat(), when.isoformat(), 1,
             int(rating == Rating.Again), int(rating)))
    else:
        card = Card.from_dict(json.loads(row["card"]))
        card, _ = _scheduler.review_card(card, rating, when)
        conn.execute(
            "UPDATE srs_cards SET card=?, due=?, reps=reps+1, lapses=lapses+?, last_rating=? WHERE id=?",
            (json.dumps(card.to_dict()), card.due.isoformat(), int(rating == Rating.Again), int(rating), cid))
    return {"due": card.due.isoformat(), "rating": RATING_LABEL[int(rating)]}


def due_cards(limit: int = 50, before: datetime | None = None, exclude: set[str] | None = None) -> list[dict]:
    before = before or db.now()
    rows = db.q("SELECT * FROM srs_cards WHERE suspended=0 AND due<=? ORDER BY due LIMIT ?",
                (before.isoformat(), limit + len(exclude or ())))
    out = [dict(r) for r in rows if not exclude or r["id"] not in exclude]
    return out[:limit]


def due_count(before: datetime | None = None) -> int:
    before = before or db.now()
    return db.q1("SELECT COUNT(*) n FROM srs_cards WHERE suspended=0 AND due<=?", (before.isoformat(),))["n"]


def forecast(days: int = 14) -> list[dict]:
    start = db.now()
    rows = db.q("SELECT due FROM srs_cards WHERE suspended=0")
    buckets = [0] * days
    overdue = 0
    for r in rows:
        due = datetime.fromisoformat(r["due"])
        delta = (due.astimezone().date() - start.astimezone().date()).days
        if delta <= 0:
            overdue += 1
        elif delta < days:
            buckets[delta] += 1
    buckets[0] += overdue
    return [{"date": (start + timedelta(days=i)).astimezone().date().isoformat(), "count": c}
            for i, c in enumerate(buckets)]
