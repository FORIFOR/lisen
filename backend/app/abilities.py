"""Listening profile: four abilities estimated from the learner's own answers.

  sound         音声識別   — hear-the-difference items (weak forms, linking, contractions, endings, numbers)
  vocab         語彙認識   — TOEIC words recognised from sound alone or inside a spoken sentence
  sentence      構文理解   — one spoken sentence whose meaning depends on its grammar
  conversation  会話理解   — questions on audio heard for the first time (no relistening)

Each estimate is a recency-weighted mean of the latest answers with a rough 95% interval. With few answers
it is marked provisional; nothing here predicts a TOEIC score.
"""
from __future__ import annotations

import json
import math

from . import db

ABILITIES = ("sound", "vocab", "sentence", "conversation")
LABEL = {"sound": "音声識別", "vocab": "語彙認識", "sentence": "構文理解", "conversation": "会話理解"}
DETAIL = {"sound": "連結・弱形・似た音", "vocab": "音声から単語を認識", "sentence": "文法と意味の処理", "conversation": "初見の自然な会話"}
# below these the ability gets extra training time (spec: sound 65%, the others 70%)
TARGET = {"sound": 0.65, "vocab": 0.70, "sentence": 0.70, "conversation": 0.70}
# which training block trains which ability
BLOCK_FOR = {"sound": "weak", "vocab": "words", "sentence": "sentence", "conversation": "new"}
WINDOW = 40
RECENCY = 0.96
MIN_RELIABLE = 8


def _rows(conn, ability: str, limit: int = WINDOW):
    if ability == "sound":
        sql = "SELECT score, detail FROM attempts WHERE kind='discrimination' ORDER BY id DESC LIMIT ?"
    elif ability == "vocab":
        # (detail.inferred marks an audio answer that *also* credited text mastery; the answer itself is real)
        sql = ("SELECT score, detail FROM attempts WHERE kind='word' AND json_extract(detail,'$.modality') IN ('audio','context')"
               " ORDER BY id DESC LIMIT ?")
    elif ability == "sentence":
        sql = "SELECT score, detail FROM attempts WHERE kind='sentence' ORDER BY id DESC LIMIT ?"
    else:
        # first listen only: re-listened clips measure memory, not comprehension
        sql = ("SELECT score, detail FROM attempts WHERE kind='comprehension'"
               " AND COALESCE(json_extract(detail,'$.relisten'), 0) = 0 ORDER BY id DESC LIMIT ?")
    return conn.execute(sql, (limit,)).fetchall()


def _estimate(scores: list[float]) -> dict:
    if not scores:
        return {"value": None, "n": 0, "low": None, "high": None, "provisional": True}
    w = [RECENCY ** i for i in range(len(scores))]           # scores are newest first
    mean = sum(s * x for s, x in zip(scores, w)) / sum(w)
    n_eff = sum(w) ** 2 / sum(x * x for x in w)
    half = 1.96 * math.sqrt(max(mean * (1 - mean), 0.02) / n_eff)
    return {"value": round(mean, 3), "n": len(scores), "low": round(max(0.0, mean - half), 3),
            "high": round(min(1.0, mean + half), 3), "provisional": len(scores) < MIN_RELIABLE}


def current(conn=None) -> dict[str, dict]:
    def run(c):
        out = {}
        for a in ABILITIES:
            est = _estimate([r["score"] for r in _rows(c, a)])
            out[a] = {"label": LABEL[a], "detail": DETAIL[a], "target": TARGET[a], **est}
        return out
    if conn is not None:
        return run(conn)
    with db.WRITE_LOCK:
        return run(db.connect())


def priority(profile: dict[str, dict]) -> str | None:
    """The ability furthest below its target (relative), among those with any evidence."""
    gaps = [(profile[a]["value"] / TARGET[a], a) for a in ABILITIES
            if profile[a]["value"] is not None and profile[a]["value"] < TARGET[a]]
    return min(gaps)[1] if gaps else None


def advice(profile: dict[str, dict]) -> str | None:
    p = priority(profile)
    if not p:
        return None
    if p == "sound" and (profile["vocab"]["value"] or 0) >= TARGET["vocab"]:
        return "単語は分かるのに音で聞き取れていません。単語暗記よりも、音の連結・弱形の聞き分けに時間を配分します"
    return {
        "sound": "音の連結・弱形・似た音の聞き分けを優先します",
        "vocab": "音声で単語を認識する練習（TOEIC 語彙）を増やします",
        "sentence": "1文ずつ構造を捉える練習を増やします",
        "conversation": "初見の会話を聞く時間を増やします",
    }[p]


def snapshot_from_session(conn, session_id: str) -> dict:
    """Ability values measured inside one session (the profile diagnostic or a checkpoint)."""
    rows = conn.execute("SELECT kind, score, detail FROM attempts WHERE session_id=?", (session_id,)).fetchall()
    buckets: dict[str, list[float]] = {a: [] for a in ABILITIES}
    for r in rows:
        d = json.loads(r["detail"] or "{}")
        if r["kind"] == "discrimination":
            buckets["sound"].append(r["score"])
        elif r["kind"] == "word" and d.get("modality") in ("audio", "context"):
            buckets["vocab"].append(r["score"])
        elif r["kind"] == "sentence":
            buckets["sentence"].append(r["score"])
        elif r["kind"] == "comprehension" and not d.get("relisten"):
            buckets["conversation"].append(r["score"])
    return {a: {"value": round(sum(v) / len(v), 3) if v else None, "n": len(v)} for a, v in buckets.items()}


def save_snapshot(conn, session_id: str, source: str) -> dict:
    snap = snapshot_from_session(conn, session_id)
    conn.execute("INSERT INTO ability_snapshots (created_at, date, session_id, source, data) VALUES (?,?,?,?,?)",
                 (db.now_iso(), db.local_date(), session_id, source, json.dumps(snap)))
    return snap


def history(conn=None) -> list[dict]:
    rows = db.q("SELECT * FROM ability_snapshots ORDER BY id") if conn is None else \
        conn.execute("SELECT * FROM ability_snapshots ORDER BY id").fetchall()
    return [{"created_at": r["created_at"], "date": r["date"], "session_id": r["session_id"], "source": r["source"],
             "data": json.loads(r["data"])} for r in rows]
