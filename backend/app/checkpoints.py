"""Are the five checkpoint forms comparable? Objective properties of each form, and how far apart they are.

These are proxies for difficulty (length, speaking rate, sentence length, long words, question types).
They show that no form is obviously easier; they do not prove equal difficulty, which would need answer
data from many learners. The per-learner random order (progress.checkpoint_plan) is what keeps any
remaining difference from looking like progress.
"""
from __future__ import annotations

import re
import statistics

from . import db, progress
from .content import library

# fixed tolerances (relative to the mean over the five forms), set from what a listener would notice
TOLERANCE = {"words": 0.15, "words_per_second": 0.10, "words_per_line": 0.15}
LONG_WORD_TOLERANCE = 0.04       # absolute, share of words with 3+ syllables
MAX_TYPE_GAP = 1                 # gist + inference questions may differ by at most one


def _syllables(w: str) -> int:
    w = w.lower()
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups) - (1 if w.endswith("e") and len(groups) > 1 and not w.endswith(("le", "ee")) else 0)
    return max(1, n)


def form_stats(form: str) -> dict:
    clips = library().test_clips(form)
    words = [w for c in clips for s in c.segments for w in re.findall(r"[A-Za-z']+", s.text)]
    speech = sum(max(0.01, s.end - s.start) for c in clips for s in c.segments)
    lines = sum(len(c.segments) for c in clips)
    types = [q.get("type") or "detail" for c in clips for q in c.questions]
    return {
        "form": form, "clips": len(clips), "questions": len(types),
        "levels": [c.level for c in clips],
        "words": len(words),
        "words_per_second": round(len(words) / speech, 2) if speech else None,
        "words_per_line": round(len(words) / lines, 1) if lines else None,
        "long_word_share": round(sum(_syllables(w) >= 3 for w in words) / len(words), 3) if words else None,
        "seconds": round(sum(c.duration for c in clips), 1),
        "gist_inference": sum(t in ("gist", "inference") for t in types),
    }


def equivalence() -> dict:
    stats = [form_stats(f) for f in progress.FORMS]
    checks = []
    for key, tol in TOLERANCE.items():
        vals = [s[key] for s in stats]
        mean = statistics.mean(vals)
        worst = max(abs(v - mean) / mean for v in vals)
        checks.append({"metric": key, "mean": round(mean, 2), "min": min(vals), "max": max(vals),
                       "max_deviation": round(worst, 3), "tolerance": tol, "ok": worst <= tol})
    vals = [s["long_word_share"] for s in stats]
    mean = statistics.mean(vals)
    worst = max(abs(v - mean) for v in vals)
    checks.append({"metric": "long_word_share", "mean": round(mean, 3), "min": min(vals), "max": max(vals),
                   "max_deviation": round(worst, 3), "tolerance": LONG_WORD_TOLERANCE, "ok": worst <= LONG_WORD_TOLERANCE})
    gi = [s["gist_inference"] for s in stats]
    checks.append({"metric": "gist_inference", "mean": round(statistics.mean(gi), 2), "min": min(gi), "max": max(gi),
                   "max_deviation": max(gi) - min(gi), "tolerance": MAX_TYPE_GAP, "ok": max(gi) - min(gi) <= MAX_TYPE_GAP})
    same_shape = len({(tuple(s["levels"]), s["questions"]) for s in stats}) == 1
    checks.insert(0, {"metric": "structure", "ok": same_shape, "detail": "全回 レベル1〜5 × 各2問"})
    return {"forms": stats, "checks": checks, "ok": all(c["ok"] for c in checks)}


def overview() -> dict:
    plan = progress.tests_status()
    results = {r["form"]: dict(r) for r in db.q("SELECT form, taken_at, day, dictation, comprehension, score FROM tests")}
    for p in plan:
        p["result"] = results.get(p["form"])
    return {"plan": plan, "equivalence": equivalence(),
            "note": "テスト音声と日程の組み合わせは利用者ごとにランダムです。ある回だけ易しくても、それが特定の時期の伸びに見えないようにするためです。"}
