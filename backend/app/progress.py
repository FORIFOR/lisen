"""Learner model: skill mastery, level unlocking, 60-day curriculum, day counting."""
from __future__ import annotations

from . import config, db
from .scoring import SKILLS

PRIOR = 0.6          # assumed accuracy before we have evidence
PRIOR_WEIGHT = 3.0
DECAY = 0.98         # older evidence slowly fades
LEVEL_DECAY = 0.92
LEVEL_NAMES = {1: "A2 基礎", 2: "B1 日常", 3: "B1+ 実用", 4: "B2 応用", 5: "C1 上級"}

PHASES = [
    (1, 10, "基礎の音", "弱形と短縮形：速く弱く発音される機能語を聞き取る", ["weak_forms", "contractions"]),
    (11, 20, "音のつながり", "単語と単語がつながる・消える音に慣れる", ["linking", "weak_forms"]),
    (21, 30, "数字と情報", "時刻・金額・数量など、聞き逃せない情報を正確に", ["numbers", "comprehension"]),
    (31, 40, "語尾と文法", "-s / -ed など文法を担う小さな音を捉える", ["endings", "contractions"]),
    (41, 50, "会話を追う", "複数話者の会話で、誰が何を言ったかを追う", ["comprehension", "vocabulary"]),
    (51, 60, "長めの理解", "講義・ニュースの要点と話者の意図を推測する", ["comprehension", "vocabulary"]),
]
MAINTENANCE = (61, 10**6, "維持と発展", "弱点データに合わせた自由トレーニング", [])


def mastery() -> dict[str, dict]:
    rows = {r["skill"]: r for r in db.q("SELECT * FROM skill_stats")}
    out = {}
    for key, label in SKILLS.items():
        r = rows.get(key)
        num, den = (r["num"], r["den"]) if r else (0.0, 0.0)
        out[key] = {"label": label, "mastery": round((num + PRIOR * PRIOR_WEIGHT) / (den + PRIOR_WEIGHT), 3),
                    "evidence": round(den, 1), "raw": round(num / den, 3) if den else None}
    return out


def update_skills(conn, stats: dict[str, list[int] | tuple[int, int]]) -> None:
    for skill, (ok, total) in stats.items():
        if total <= 0:
            continue
        conn.execute(
            """INSERT INTO skill_stats (skill, num, den, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(skill) DO UPDATE SET num=num*?+excluded.num, den=den*?+excluded.den,
               updated_at=excluded.updated_at""",
            (skill, ok, total, db.now_iso(), DECAY, DECAY))


def update_level(conn, level: int, dictation: float | None = None, comprehension: float | None = None) -> dict | None:
    """Record performance at a level and unlock / adjust the learner's level.
    Material unlocks by demonstrated ability, not by the number of days."""
    conn.execute("INSERT OR IGNORE INTO level_stats (level) VALUES (?)", (level,))
    if dictation is not None:
        conn.execute("UPDATE level_stats SET dict_num=dict_num*?+?, dict_den=dict_den*?+1, attempts=attempts+1 WHERE level=?",
                     (LEVEL_DECAY, dictation, LEVEL_DECAY, level))
    if comprehension is not None:
        conn.execute("UPDATE level_stats SET comp_num=comp_num*?+?, comp_den=comp_den*?+1 WHERE level=?",
                     (LEVEL_DECAY, comprehension, LEVEL_DECAY, level))
    prof = conn.execute("SELECT level, unlocked FROM profile WHERE id=1").fetchone()
    if level != prof["level"]:
        return None
    st = conn.execute("SELECT * FROM level_stats WHERE level=?", (level,)).fetchone()
    d = st["dict_num"] / st["dict_den"] if st["dict_den"] else 0
    c = st["comp_num"] / st["comp_den"] if st["comp_den"] else 0
    if level < 5 and st["attempts"] >= 12 and st["comp_den"] >= 3 and d >= 0.85 and c >= 0.75:
        conn.execute("UPDATE profile SET level=?, unlocked=MAX(unlocked, ?) WHERE id=1", (level + 1, level + 1))
        return {"event": "level_up", "from": level, "to": level + 1}
    if level > 1 and st["attempts"] >= 10 and d < 0.5:
        conn.execute("UPDATE profile SET level=? WHERE id=1", (level - 1,))
        conn.execute("UPDATE level_stats SET dict_num=0, dict_den=0, attempts=0 WHERE level=?", (level,))
        return {"event": "level_adjust", "from": level, "to": level - 1}
    return None


def level_progress() -> list[dict]:
    rows = {r["level"]: r for r in db.q("SELECT * FROM level_stats")}
    prof = db.profile()
    out = []
    for lv in range(1, 6):
        r = rows.get(lv)
        out.append({
            "level": lv, "name": LEVEL_NAMES[lv], "unlocked": lv <= prof["unlocked"], "current": lv == prof["level"],
            "dictation": round(r["dict_num"] / r["dict_den"], 3) if r and r["dict_den"] else None,
            "comprehension": round(r["comp_num"] / r["comp_den"], 3) if r and r["comp_den"] else None,
            "attempts": r["attempts"] if r else 0,
        })
    return out


def study_days() -> list[str]:
    """Dates with at least 5 minutes of study."""
    return [r["date"] for r in db.q("SELECT date FROM daily_log WHERE seconds>=300 ORDER BY date")]


def day_number() -> int:
    days = study_days()
    today = db.local_date()
    return len(days) + (0 if today in days else 1)


def phase(day: int | None = None) -> dict:
    day = day or day_number()
    for start, end, name, desc, focus in PHASES + [MAINTENANCE]:
        if start <= day <= end:
            return {"start": start, "end": end if end < 10**6 else None, "name": name,
                    "description": desc, "focus": focus}
    raise AssertionError


def curriculum() -> list[dict]:
    return [{"start": s, "end": e, "name": n, "description": d, "focus": f} for s, e, n, d, f in PHASES]


def streak() -> int:
    from datetime import date, timedelta
    days = set(study_days())
    cur = date.fromisoformat(db.local_date())
    if cur.isoformat() not in days:
        cur -= timedelta(days=1)
    n = 0
    while cur.isoformat() in days:
        n += 1
        cur -= timedelta(days=1)
    return n


def today_seconds() -> int:
    r = db.q1("SELECT seconds FROM daily_log WHERE date=?", (db.local_date(),))
    return r["seconds"] if r else 0


TEST_SCHEDULE = {"A": 1, "B": 30, "C": 60}
TEST_NAMES = {"A": "初回レベル診断", "B": "Day 30 中間テスト", "C": "Day 60 最終テスト"}


def tests_status() -> list[dict]:
    taken = {r["form"]: dict(r) for r in db.q("SELECT * FROM tests ORDER BY taken_at")}
    day = day_number()
    out = []
    for form, due_day in TEST_SCHEDULE.items():
        t = taken.get(form)
        out.append({"form": form, "name": TEST_NAMES[form], "day": due_day, "available": day >= due_day,
                    "taken": bool(t), "score": t["score"] if t else None,
                    "taken_at": t["taken_at"] if t else None})
    return out


def config_summary() -> dict:
    return {"daily_goal": db.profile()["settings"]["daily_goal"], "curriculum_days": config.CURRICULUM_DAYS}
