from __future__ import annotations

import json
from datetime import date, timedelta

from . import db, progress, srs
from .scoring import SKILLS


def overview() -> dict:
    today = date.fromisoformat(db.local_date())
    logs = {r["date"]: r["seconds"] for r in db.q("SELECT * FROM daily_log")}
    daily = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        daily.append({"date": d, "minutes": round(logs.get(d, 0) / 60, 1)})

    acc_rows = db.q("SELECT date, kind, AVG(score) s, COUNT(*) n FROM attempts WHERE date>=? GROUP BY date, kind",
                    ((today - timedelta(days=29)).isoformat(),))
    trend: dict[str, dict] = {}
    for r in acc_rows:
        trend.setdefault(r["date"], {"date": r["date"]})[r["kind"]] = round(r["s"], 3)
    trend_list = [trend[d] for d in sorted(trend)]

    errors: dict[str, int] = {}
    for r in db.q("SELECT detail FROM attempts WHERE kind IN ('dictation','review') AND date>=?",
                  ((today - timedelta(days=13)).isoformat(),)):
        for k, n in json.loads(r["detail"]).get("errors", {}).items():
            errors[k] = errors.get(k, 0) + n

    totals = db.q1("SELECT COUNT(*) n, COUNT(DISTINCT item_id) u FROM attempts")
    clips_heard = db.q1("SELECT COUNT(*) n FROM exposures WHERE kind='clip'")["n"]
    tests = [dict(r) for r in db.q("SELECT form, taken_at, day, dictation, comprehension, score, level_estimate FROM tests ORDER BY taken_at")]
    m = progress.mastery()
    weakest = sorted(((v["mastery"], k) for k, v in m.items() if v["evidence"] >= 3))[:2]
    return {
        "total_minutes": round(sum(logs.values()) / 60),
        "study_days": len(progress.study_days()),
        "streak": progress.streak(),
        "day": progress.day_number(),
        "daily": daily,
        "trend": trend_list,
        "mastery": [{"skill": k, **v} for k, v in m.items()],
        "weakest": [{"skill": k, "label": SKILLS[k], "mastery": s} for s, k in weakest],
        "errors": [{"skill": k, "label": SKILLS[k], "count": n} for k, n in sorted(errors.items(), key=lambda x: -x[1])],
        "levels": progress.level_progress(),
        "items_done": totals["n"],
        "clips_heard": clips_heard,
        "review_forecast": srs.forecast(14),
        "cards": db.q1("SELECT COUNT(*) n FROM srs_cards")["n"],
        "tests": tests,
        "curriculum": progress.curriculum(),
        "phase": progress.phase(),
    }


def kpis() -> dict:
    """The measures that say whether listening is actually improving (spec section 8)."""
    today = date.fromisoformat(db.local_date())

    def window(sql_where: str, days: int, offset: int = 0, extra: tuple = ()) -> dict:
        hi = (today - timedelta(days=offset)).isoformat()
        lo = (today - timedelta(days=offset + days - 1)).isoformat()
        r = db.q1(f"SELECT AVG(score) s, COUNT(*) n FROM attempts WHERE {sql_where} AND date>=? AND date<=?", (*extra, lo, hi))
        return {"value": round(r["s"], 3) if r["n"] else None, "n": r["n"]}

    def pair(where: str) -> dict:
        return {"recent": window(where, 14), "previous": window(where, 14, 14)}

    unseen = "kind='comprehension' AND COALESCE(json_extract(detail,'$.relisten'),0)=0"
    days = set(progress.study_days())
    def rate(n: int) -> float:
        return round(sum(1 for i in range(n) if (today - timedelta(days=i)).isoformat() in days) / n, 3)
    return {
        "unseen_comprehension": {"label": "初見会話理解率", "note": "最重要。初めて聞く音声の設問正答率", **pair(unseen)},
        "first_play": {"label": "初回再生での理解率", "note": "1回だけ再生して答えた初見音声",
                       **pair(unseen + " AND json_extract(detail,'$.plays')=1")},
        "sound": {"label": "音声識別正答率", "note": "似た音・弱形・連結の聞き分け", **pair("kind='discrimination'")},
        "transfer": {"label": "応用文の理解率", "note": "練習した表現を、初めて聞く別の文で理解できた割合",
                     **pair("kind='transfer'")},
        "audio_vocab": {"label": "音声語彙認識率", "note": "TOEIC 語彙を音・文脈で認識",
                        **pair("kind='word' AND json_extract(detail,'$.modality') IN ('audio','context')")},
        "retention": {"label": "継続率", "note": "直近7日・30日のうち学習した日の割合", "d7": rate(7), "d30": rate(30)},
    }
