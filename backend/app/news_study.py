"""Daily English News, the learner's side: one lesson in five steps.

    1. listen   the whole story by ear, no text (the Japanese summary is not shown yet either)
    2. answer   3 comprehension questions; results are shown only after all three, and only then the text
    3. read     each sentence with its translation and audio; tap a word to add it to the word book
    4. unseen   up to 3 sentences in another situation using this story's words, at normal speed
    5. done     first-listen score and unseen-sentence score, kept apart (and apart from the word book's results)

Answers are stored once per (lesson, level, step, question): the same answer again returns the stored result,
a different one is refused. Words missed in step 4 are added to the word book automatically.
"""
from __future__ import annotations

import json
import logging
import threading

from . import db, news, tts
from . import news_feeds as F
from .vocab_data import NEWS_DATASET, news_words_path, reload_vocab_library, vocab_library

log = logging.getLogger(__name__)
_words_lock = threading.Lock()
PHASE_LABEL = {"first": "初めて聞いたときの理解", "transfer": "別の文での確認"}


def _row(lesson_id: str) -> dict:
    r = db.q1("SELECT * FROM news_lessons WHERE id=?", (lesson_id,))
    if not r or r["status"] != "published":
        raise news.NewsError(404, "このニュース教材は見つかりません")
    return {**dict(r), "data": json.loads(r["data"]), "audio": json.loads(r["audio"])}


def levels_ok(r: dict) -> list[str]:
    return [lv for lv in news.LEVELS if (r["audio"]["levels"].get(lv) or {}).get("status") == "ok"]


def _answers(lesson_id: str) -> dict:
    out: dict = {}
    for a in db.q("SELECT * FROM news_answers WHERE lesson_id=?", (lesson_id,)):
        out.setdefault((a["level"], a["phase"]), {})[a["idx"]] = dict(a)
    return out


def _source(d: dict) -> dict:
    s = d["source"]
    return {"publisher": s["publisher"], "kind": s["kind"], "kind_label": F.KIND_LABEL.get(s["kind"], s["kind"]),
            "url": s["url"], "title": s["title"], "published_at": s["published_at"],
            "category": s["category"], "category_label": F.CATEGORIES.get(s["category"], s["category"])}


def card(r: dict) -> dict:
    """A lesson in the list: the English headline only (the Japanese summary would give the story away)."""
    d = r["data"]
    lv = levels_ok(r)
    ans = _answers(r["id"])
    first = {lvl for (lvl, ph), v in ans.items() if ph == "first" and len(v) >= news.QUESTIONS}
    checks = len(d.get("transfer_checks") or [])
    unseen = len(ans.get(("all", "transfer"), {}))
    state = "done" if first and unseen >= checks else "listened" if first else "new"
    pref = news.settings()["level"]
    secs = (r["audio"]["levels"].get(pref if pref in lv else lv[0]) or {}).get("seconds", 0)
    return {"id": r["id"], "date": r["date"], "title_en": d["title_en"], "title_ja": d["title_ja"] if state != "new" else None,
            "source": _source(d), "corroborated": sorted({c["publisher"] for c in d.get("corroborated") or []}),
            "levels": lv, "minutes": max(1, round(secs / 60 + 1.5)), "state": state}


def overview(days: int = 7) -> dict:
    today = db.local_date()
    rows = db.q("SELECT * FROM news_lessons WHERE status='published' ORDER BY date DESC, created_at LIMIT 60")
    items = [card({**dict(r), "data": json.loads(r["data"]), "audio": json.loads(r["audio"])}) for r in rows]
    import datetime as dt
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=days - 1)).isoformat()
    return {"date": today, "today": [c for c in items if c["date"] == today],
            "recent": [c for c in items if since <= c["date"] < today],
            "status": news.status(), "progress": progress()}


def view(lesson_id: str, level: str | None = None) -> dict:
    r = _row(lesson_id)
    d = r["data"]
    lv_ok = levels_ok(r)
    level = level if level in lv_ok else (news.settings()["level"] if news.settings()["level"] in lv_ok else lv_ok[0])
    L = d["levels"][level]
    A = r["audio"]["levels"][level]
    ans = _answers(lesson_id)
    first = ans.get((level, "first"), {})
    first_done = len(first) >= news.QUESTIONS
    out = {"id": lesson_id, "date": r["date"], "level": level, "levels": lv_ok, "level_labels": news.LEVEL_LABEL,
           "title_en": d["title_en"], "source": _source(d),
           "corroborated": sorted({c["publisher"] for c in d.get("corroborated") or []}),
           "audio": A["full"], "seconds": A["seconds"],
           "questions": [{"q": q["q"], "choices": q["choices"]} for q in L["questions"]],
           "first_done": first_done, "first_answered": sorted(first)}
    if first_done:
        out.update(_reveal(d, level, A, first))
        tr = ans.get(("all", "transfer"), {})
        out["transfer"] = [{"index": i, "lemma": c["lemma"], "audio": r["audio"]["transfer"].get(c["lemma"]),
                            "choices": c["choices"], "answered": _transfer_fb(c, tr[i]) if i in tr else None}
                           for i, c in enumerate(d.get("transfer_checks") or [])]
    return out


def _reveal(d: dict, level: str, A: dict, first: dict) -> dict:
    L = d["levels"][level]
    added = {r["lemma"] for r in db.q("SELECT lemma FROM news_words")}
    lib = vocab_library()
    vocab = []
    for v in d.get("vocabulary") or []:
        wid = v.get("word_id") or (f"news-{v['lemma']}" if f"news-{v['lemma']}" in lib.words else None)
        book = lib.words.get(wid) if wid else None
        forms = book.forms() if book else {v["lemma"], v["form"]}
        idx = next((i for i, s in enumerate(L["sentences"]) if any(_has(s["en"], f) for f in forms)), None)
        vocab.append({"lemma": v["lemma"], "pos": v["pos"], "meaning": v["meaning"], "form": v["form"], "sentence": idx,
                      "in_book": bool(v.get("word_id")), "added": v["lemma"] in added})
    return {"title_ja": d["title_ja"], "summary_ja": d["summary_ja"],
            "sentences": [{"en": s["en"], "ja": s["ja"], "audio": A["sentences"][i]} for i, s in enumerate(L["sentences"])],
            "results": [_first_fb(q, first[i]) for i, q in enumerate(L["questions"])], "vocabulary": vocab}


def _has(text: str, form: str) -> bool:
    import re
    return bool(re.search(rf"\b{re.escape(form)}\b", text, re.I))


def _first_fb(q: dict, a: dict) -> dict:
    return {"q": q["q"], "choices": q["choices"], "answer": q["answer"], "choice": a["choice"], "correct": bool(a["correct"]),
            "explanation": q["explanation"], "evidence": q["evidence"]}


def _transfer_fb(c: dict, a: dict) -> dict:
    return {"correct": bool(a["correct"]), "answer": c["answer"], "choice": a["choice"], "en": c["en"], "ja": c["ja"],
            "form": c["form"], "meaning": c["choices"][c["answer"]]}


def answer(lesson_id: str, level: str, phase: str, index: int, choice: int | None, ms: int) -> dict:
    r = _row(lesson_id)
    d = r["data"]
    if phase == "first":
        if level not in levels_ok(r):
            raise news.NewsError(422, "このレベルの教材はありません")
        qs = d["levels"][level]["questions"]
        if not 0 <= index < len(qs):
            raise news.NewsError(422, "問題の番号が正しくありません")
        right = qs[index]["answer"]
        key_level = level
    elif phase == "transfer":
        checks = d.get("transfer_checks") or []
        if not 0 <= index < len(checks):
            raise news.NewsError(422, "問題の番号が正しくありません")
        done = [lv for (lv, ph), v in _answers(lesson_id).items() if ph == "first" and len(v) >= news.QUESTIONS]
        if not done:                # the questions of any level first (the unseen sentences are shared)
            raise news.NewsError(409, "先に内容の問題に答えてください")
        right = checks[index]["answer"]
        key_level = "all"           # the unseen sentences are the same at every level: answered once
    else:
        raise news.NewsError(422, "phase は first か transfer です")
    if choice is not None and not 0 <= choice < 4:
        raise news.NewsError(422, "選択肢の番号が正しくありません")
    correct = choice is not None and choice == right
    ms = max(0, min(int(ms or 0), 600_000))
    with db.tx() as conn:
        prev = conn.execute("SELECT * FROM news_answers WHERE lesson_id=? AND level=? AND phase=? AND idx=?",
                            (lesson_id, key_level, phase, index)).fetchone()
        if prev:
            if prev["choice"] != choice:
                raise news.NewsError(409, "この問題は回答済みです（別の端末で回答された可能性があります）")
            duplicate = True
        else:
            conn.execute("INSERT INTO news_answers (lesson_id, level, phase, idx, choice, correct, ms, created_at, date)"
                         " VALUES (?,?,?,?,?,?,?,?,?)",
                         (lesson_id, key_level, phase, index, choice, int(correct), ms, db.now_iso(), db.local_date()))
            duplicate = False
    if phase == "first":
        got = _answers(lesson_id).get((level, "first"), {})
        out = {"phase": "first", "duplicate": duplicate, "answered": len(got), "done": len(got) >= news.QUESTIONS}
        if out["done"]:
            out["lesson"] = view(lesson_id, level)
        return out
    c = d["transfer_checks"][index]
    added = None
    if not correct and not duplicate:
        added = add_word(lesson_id, c["lemma"], origin="missed")
    a = {"choice": choice, "correct": int(correct)}
    return {"phase": "transfer", "duplicate": duplicate, **_transfer_fb(c, a), "added": added}


# --- the word book ------------------------------------------------------------------------------------

def add_word(lesson_id: str, lemma: str, origin: str = "tap") -> dict:
    """Put a word from a news lesson into the word book, once. A word the book already has is queued for the
    next word lesson; a new word becomes a personal word-book entry (the news sentence as its example, the unseen
    sentence as its transfer), and its audio is made and checked in the background."""
    r = _row(lesson_id)
    v = next((x for x in r["data"].get("vocabulary") or [] if x["lemma"] == lemma), None)
    lib = vocab_library()
    book = lib.by_lemma(lemma)
    if not v and not book:
        raise news.NewsError(404, "この単語は追加できません（意味の情報がありません）")
    prev = db.q1("SELECT * FROM news_words WHERE lemma=?", (lemma,))
    if prev:
        return {"status": "already", "lemma": lemma, "word_id": prev["word_id"], "message": "単語帳に追加済みです"}
    if book:
        wid, status = book.id, "queued"
    else:
        wid = f"news-{lemma}"
        status = "created"
        with _words_lock:
            if wid not in vocab_library().words:
                vl = r["data"].get("vocab_level", "B1")
                entry = news._word_entry(v, r["data"]["levels"][vl]["sentences"], news.LEVEL_TIER[vl],
                                         r["data"]["source"]["category"])
                p = news_words_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                cur = json.loads(p.read_text()) if p.exists() else {
                    "dataset": {"name": "News words", "license": "personal (not redistributed)",
                                "source": "このアプリで作成（ニュースの要約から Gemini が書いた例文。各語の出典はニュース教材に記録）"},
                    "words": []}
                cur["words"] = [w for w in cur["words"] if w["id"] != wid] + [{**entry, "news_lesson": lesson_id}]
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1))
                tmp.replace(p)
                lib = reload_vocab_library()
                if wid not in lib.words:
                    raise news.NewsError(422, "単語帳の形式に合わないため追加できませんでした")
        threading.Thread(target=_make_word_audio, args=(wid,), daemon=True).start()
    with db.tx() as conn:
        conn.execute("INSERT OR IGNORE INTO news_words (lemma, word_id, lesson_id, origin, added_at) VALUES (?,?,?,?,?)",
                     (lemma, wid, lesson_id, origin, db.now_iso()))
        conn.execute("INSERT OR IGNORE INTO user_vocab (word_id, first_seen_at) VALUES (?,?)", (wid, db.now_iso()))
    return {"status": status, "lemma": lemma, "word_id": wid,
            "message": "次の単語の学習で最初に出します" if status == "queued" else "単語帳に追加しました（次の単語の学習で最初に出します）"}


def _make_word_audio(wid: str) -> None:
    """Word, news sentence and unseen sentence audio for a new word-book entry; a sentence whose audio fails the
    speech-recognition check is held (not used) like the built-in words."""
    try:
        with _words_lock:
            lib = vocab_library()
            w = lib.words.get(wid)
            if not w:
                return
            tts.build_vocab_audio([w], workers=1, log=lambda *_: None)
            lib = reload_vocab_library()
            files = lib.files(w)
            items = [(kind, text) for kind, text in (("example", w.example), ("transfer", w.transfer)) if text and kind in files]
            from .config import AUDIO_DIR
            heard = news.asr_check([(news.spoken(t), AUDIO_DIR / files[k]) for k, t in items])     # '£85m' is spoken
            hold = sorted(k for (k, _), ok in zip(items, heard) if ok is False)
            missing = sorted(k for k in ("example", "transfer") if k not in files)
            if hold or missing:
                p = news_words_path()
                cur = json.loads(p.read_text())
                for e in cur["words"]:
                    if e["id"] == wid:
                        e["audio_hold"] = sorted(set(hold) | set(missing))
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1))
                tmp.replace(p)
                reload_vocab_library()
    except Exception:  # noqa: BLE001 - background work; the word stays usable in writing
        log.exception("news word audio %s", wid)


def progress() -> dict:
    out = {}
    for ph in ("first", "transfer"):
        r = db.q1("SELECT COUNT(*) n, AVG(correct) s FROM news_answers WHERE phase=?", (ph,))
        out[ph] = {"label": PHASE_LABEL[ph], "n": r["n"], "accuracy": round(r["s"], 3) if r["n"] else None}
    by_level = {}
    for r in db.q("SELECT level, COUNT(*) n, AVG(correct) s FROM news_answers WHERE phase='first' GROUP BY level"):
        by_level[r["level"]] = {"n": r["n"], "accuracy": round(r["s"], 3)}
    lessons_done = db.q1("SELECT COUNT(DISTINCT lesson_id) n FROM news_answers WHERE phase='first'")["n"]
    words = db.q1("SELECT COUNT(*) n FROM news_words")["n"]
    return {**out, "by_level": by_level, "lessons": lessons_done, "words": words, "dataset": NEWS_DATASET}
