"""Word lessons (5 / 10 / 15 / 30 min): re-learn forgotten words, then prove it by ear.

For each word:
  1. study    – the word is shown and played; the learner tries to recall the meaning, then sees the
                meaning, part of speech, scene and an example sentence with its translation (self-report only:
                recalling a meaning from the written word never marks the word as learned)
  2. first    – a few items later: the word alone by ear (4 choices)
  3. first    – the study example by ear
  4. transfer – a sentence the learner has never seen (another scene, another speaker), at normal speed
Words come from the placement test's routes first (basic -> sound -> context), then forgotten words,
then new words at the estimated level. FSRS reviews that are due take up to 40% of the time.
Results of placement, first checks, transfer and review are logged with their phase and reported apart.
"""
from __future__ import annotations

import json
import random
import uuid

from . import db
from . import vocab as V
from .vocab_data import vocab_library

MINUTES = (5, 10, 15, 30)
ITEM_SECONDS = {"study": 35, "text": 10, "audio": 12, "context": 18}
REVIEW_SHARE = 0.4
OVERRUN_S = 120          # finish the checks of words already studied, up to two minutes past the budget
STEPS = (("audio", "example", "first", 2), ("context", "example", "first", 4), ("context", "transfer", "transfer", 7))


class LessonError(V.VocabError):
    pass


def _load(conn, lid: str) -> dict:
    row = conn.execute("SELECT * FROM vocab_lessons WHERE id=?", (lid,)).fetchone()
    if not row:
        raise LessonError(404, "単語レッスンが見つかりません")
    d = dict(row)
    d["state"] = json.loads(d["state"])
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d


def _save(conn, d: dict) -> None:
    conn.execute("UPDATE vocab_lessons SET status=?, updated_at=?, completed_at=?, state=?, result=? WHERE id=?",
                 (d["status"], db.now_iso(), d.get("completed_at"), json.dumps(d["state"]),
                  json.dumps(d["result"], ensure_ascii=False) if d["result"] else None, d["id"]))


def public(d: dict) -> dict:
    st = d["state"]
    return {"id": d["id"], "status": d["status"], "minutes": d["minutes"], "spent_s": int(st["spent"]),
            "budget_s": st["budget"], "studied": len(st["studied"]), "answered": st["answered"],
            "created_at": d["created_at"], "result": d["result"]}


def start(minutes: int, replace: bool = False) -> dict:
    if minutes not in MINUTES:
        raise LessonError(422, "時間は 5・10・15・30 分から選んでください")
    with db.tx() as conn:
        active = conn.execute("SELECT id FROM vocab_lessons WHERE status='active' ORDER BY created_at DESC").fetchall()
        if active and not replace:
            return {**public(_load(conn, active[0]["id"])), "resumed": True}
        carry: list[dict] = []
        for r in active:
            old = _load(conn, r["id"])
            carry += old["state"]["queue"]
            conn.execute("UPDATE vocab_lessons SET status='abandoned', updated_at=? WHERE id=?", (db.now_iso(), r["id"]))
            conn.execute("UPDATE vocab_items SET answered=2 WHERE origin='learn' AND origin_id=? AND answered=0", (r["id"],))
        last = conn.execute("SELECT state FROM vocab_lessons WHERE status='completed' ORDER BY completed_at DESC LIMIT 1").fetchone()
        if last:
            carry += json.loads(last["state"]).get("leftover", [])
        # checks left over from the previous lesson come first (their words were studied but not yet tested)
        seen, queue = set(), []
        for q in carry:
            key = (q["word"], q["modality"], q["sentence"])
            if key not in seen and q["word"] in vocab_library().words:
                seen.add(key)
                queue.append({**q, "after": 0})
        lid = uuid.uuid4().hex[:12]
        state = {"budget": minutes * 60, "spent": 0.0, "queue": queue, "studied": [], "reviews": 0,
                 "review_s": 0.0, "answered": 0, "results": {}, "leftover": [], "retried": []}
        conn.execute("INSERT INTO vocab_lessons (id, status, minutes, created_at, updated_at, state) VALUES (?,?,?,?,?,?)",
                     (lid, "active", minutes, db.now_iso(), db.now_iso(), json.dumps(state)))
        return {**public(_load(conn, lid)), "resumed": False}


# --- choosing words --------------------------------------------------------------------------

def _studied_recently(conn) -> set[str]:
    rows = conn.execute("SELECT DISTINCT item_id FROM attempts WHERE kind='word_study' AND date>=date(?, '-1 day')",
                        (db.local_date(),)).fetchall()
    return {r["item_id"] for r in rows}


def choose_word(conn, exclude: set[str], rng: random.Random) -> tuple[str, str] | None:
    """(word_id, why) for the next word to study."""
    lib = vocab_library()
    skip = exclude | _studied_recently(conn)
    rows = {r["word_id"]: r for r in conn.execute("SELECT * FROM user_vocab").fetchall()}
    last = conn.execute("SELECT result FROM vocab_diagnostics WHERE status='completed' AND result IS NOT NULL"
                        " ORDER BY completed_at DESC LIMIT 1").fetchone()
    if last:
        routes = json.loads(last["result"]).get("routes") or {}
        for key, why in (("basic", "診断で意味が分からなかった単語"), ("sound", "診断で文字なら分かったが音で聞き取れなかった単語"),
                         ("context", "診断で例文の中では分からなかった単語")):
            for b in routes.get(key, []):
                wid = b["id"]
                if wid in lib.words and wid not in skip and V.status_of(rows.get(wid)) not in ("acquired", "confirm"):
                    return wid, why
    weak = [(r["text_mastery"] if r["text_n"] else 0.0, wid) for wid, r in rows.items()
            if wid in lib.words and wid not in skip and V.status_of(r) in ("forgotten", "unknown")]
    if weak:
        return min(weak)[1], "忘れかけている単語"
    tier = V.estimated_tier(conn)
    for t in (tier, min(4, tier + 1), max(1, tier - 1), 1, 2, 3, 4):
        fresh = [w for w in lib.by_tier(t) if w.id not in rows and w.id not in skip]
        if fresh:
            # the more frequent words first (TSL / NGSL rank), with a little variety
            fresh.sort(key=lambda w: (w.rank or 9999) + rng.random() * 40)
            return fresh[0].id, "新しい単語"
    return None


# --- items -------------------------------------------------------------------------------------

def study_item(word_id: str, why: str) -> dict:
    w = vocab_library().words[word_id]
    audio = vocab_library().audio(w)
    pub = {"type": "word_study", "lemma": w.lemma, "pos": w.pos, "audio": audio.get("word"), "tier": w.tier,
           "reason": why, "prompt": "意味を思い出してから「答えを見る」を押してください"}
    return {"type": "word_study", "public": pub, "secret": {"word_id": w.id}}


def study_feedback(w) -> dict:
    audio = vocab_library().audio(w)
    return {"type": "word_study", "lemma": w.lemma, "pos": w.pos, "meaning": w.meaning, "scene": w.scene,
            "example": w.example, "form": w.form, "example_ja": w.example_ja, "word_audio": audio.get("word"),
            "example_audio": audio.get("example"), "tier": w.tier}


def grade_study(conn, payload: dict, body: dict, lesson_id: str | None) -> dict:
    """A self-report: logged, but it changes no mastery and no FSRS card."""
    w = vocab_library().words[payload["secret"]["word_id"]]
    recalled = body.get("recall") or "no"
    ms = max(0, min(int(body.get("ms") or 0), 600_000))
    conn.execute("INSERT INTO attempts (session_id, created_at, date, kind, block, item_id, level, score, detail)"
                 " VALUES (?,?,?,?,?,?,?,?,?)",
                 (lesson_id, db.now_iso(), db.local_date(), "word_study", "learn", w.id, w.tier, None,
                  json.dumps({"recall": recalled, "ms": ms, "phase": "study"})))
    conn.execute("INSERT OR IGNORE INTO user_vocab (word_id, first_seen_at) VALUES (?,?)", (w.id, db.now_iso()))
    return {**study_feedback(w), "recall": recalled}


def _test_item(word_id: str, modality: str, sentence: str, phase: str, rng: random.Random) -> dict | None:
    w = vocab_library().words[word_id]
    if sentence == "transfer" and not w.transfer:
        return None
    reason = {"first": "覚えた単語を音で確認", "transfer": "初めて聞く文で確認（等速）"}[phase]
    p = V.build_item(w, modality, rng, reason=reason, sentence=sentence, phase=phase)
    if p:
        p["secret"]["enroll"] = True        # studied words get an FSRS card even when the first check is right
    return p


def next_item(lid: str) -> dict:
    rng = random.Random()
    with db.tx() as conn:
        d = _load(conn, lid)
        if d["status"] != "active":
            return {"lesson": public(d), "item": None, "done": d["status"] == "completed"}
        pending = conn.execute("SELECT payload FROM vocab_items WHERE origin='learn' AND origin_id=? AND answered=0"
                               " ORDER BY created_at DESC LIMIT 1", (lid,)).fetchone()
        if pending:
            return {"lesson": public(d), "item": json.loads(pending["payload"])["public"], "done": False}
        st = d["state"]
        over = st["spent"] >= st["budget"]
        payload = None
        exclude = set(st["studied"])
        # 1. checks of words studied in this lesson (or left over from the last one)
        while payload is None and st["queue"]:
            ready = [q for q in st["queue"] if q["after"] <= st["answered"] or over]
            if not ready:
                break
            if st["spent"] >= st["budget"] + OVERRUN_S:
                break
            q = ready[0]
            st["queue"].remove(q)
            payload = _test_item(q["word"], q["modality"], q["sentence"], q["phase"], rng)
        # 2. FSRS reviews that are due, up to their share of the time
        if payload is None and not over and st["review_s"] < st["budget"] * REVIEW_SHARE:
            payload = V.pick_next(conn, exclude | {q["word"] for q in st["queue"]}, rng, due=True, advance=False, new=False)
            if payload:
                payload["secret"]["phase"] = "review"
                payload["secret"]["review"] = True
        # 3. a new word to study (not when only the leftover checks would fit)
        if payload is None and st["spent"] + ITEM_SECONDS["study"] < st["budget"]:
            pick = choose_word(conn, exclude | {q["word"] for q in st["queue"]}, rng)
            if pick:
                wid, why = pick
                payload = study_item(wid, why)
                st["studied"].append(wid)
                for mod, sentence, phase, gap in STEPS:
                    st["queue"].append({"word": wid, "modality": mod, "sentence": sentence, "phase": phase,
                                        "after": st["answered"] + gap})
        # 4. nothing fits: checks still waiting are served before finishing
        if payload is None and st["queue"] and st["spent"] < st["budget"] + OVERRUN_S:
            q = st["queue"].pop(0)
            payload = _test_item(q["word"], q["modality"], q["sentence"], q["phase"], rng)
        if payload is None:
            _complete(conn, d)
            _save(conn, d)
            return {"lesson": public(d), "item": None, "done": True}
        payload["public"]["lesson"] = {"spent_s": int(st["spent"]), "budget_s": st["budget"]}
        item = V._insert_item(conn, "learn", lid, payload)
        _save(conn, d)
        return {"lesson": public(d), "item": item, "done": False}


def after_answer(conn, lid: str, payload: dict, fb: dict, body: dict) -> dict:
    d = _load(conn, lid)
    st = d["state"]
    kind = payload["type"]
    modality = payload["secret"].get("modality", "study")
    ms = int(body.get("ms") or 0)
    # real time when the device sent it (bounded), otherwise the typical time for this kind of item
    secs = min(max(ms / 1000, 3), 120) + 2 if ms else ITEM_SECONDS["study" if kind == "word_study" else modality]
    st["spent"] += secs
    st["answered"] += 1
    if kind == "word":
        phase = fb.get("phase") or "practice"
        st["results"].setdefault(phase, []).append(fb["score"])
        if phase == "review":
            st["reviews"] += 1
            st["review_s"] += secs
        sec = payload["secret"]
        key = f"{sec['word_id']}:{modality}:{sec.get('sentence', 'example')}"
        if phase == "first" and fb["score"] < 0.8 and key not in st["retried"]:
            # missed right after studying: hear the answer, then the same check once more a little later
            st["retried"].append(key)
            st["queue"].append({"word": sec["word_id"], "modality": modality, "sentence": sec.get("sentence", "example"),
                                "phase": "first", "after": st["answered"] + 3})
    out = {**fb, "lesson": public(d)}
    _save(conn, d)
    return out


def _complete(conn, d: dict) -> None:
    lib = vocab_library()
    st = d["state"]
    st["leftover"] = [q for q in st["queue"] if q["phase"] in ("first", "transfer")]
    st["queue"] = []
    d["status"] = "completed"
    d["completed_at"] = db.now_iso()

    def acc(xs):
        return {"accuracy": round(sum(1 for x in xs if x >= 0.8) / len(xs), 3), "n": len(xs)} if xs else None

    studied = []
    for wid in st["studied"]:
        row = conn.execute("SELECT * FROM user_vocab WHERE word_id=?", (wid,)).fetchone()
        s = V.status_of(row)
        w = lib.words[wid]
        studied.append({"id": wid, "lemma": w.lemma, "meaning": w.meaning, "status": s, "status_label": V.STATUS_LABEL[s],
                        "next_review_at": row["next_review_at"] if row else None})
    d["result"] = {
        "studied": studied,
        "phases": {p: acc(st["results"].get(p, [])) for p in ("first", "transfer", "review")},
        "phase_labels": {p: V.PHASES[p] for p in ("first", "transfer", "review")},
        "reviews": st["reviews"],
        "spent_s": int(st["spent"]),
        "leftover": len(st["leftover"]),
        "due_now": V.due_count(conn),
    }


def get(lid: str) -> dict:
    with db.tx() as conn:
        return public(_load(conn, lid))


def overview() -> dict:
    """Counts for the word book's home: learned / due / not yet studied, plus results by phase."""
    lib = vocab_library()
    rows = {r["word_id"]: r for r in db.q("SELECT * FROM user_vocab")}
    counts = {"acquired": 0, "learning": 0, "untested": 0}
    for wid in lib.words:
        s = V.status_of(rows.get(wid))
        counts["acquired" if s == "acquired" else "untested" if s == "untested" else "learning"] += 1
    by_phase: dict[str, list[float]] = {}
    for r in db.q("SELECT score, detail FROM attempts WHERE kind='word' AND score IS NOT NULL"):
        ph = (json.loads(r["detail"] or "{}").get("phase")) or "practice"
        by_phase.setdefault(ph, []).append(r["score"])
    phases = {p: ({"accuracy": round(sum(1 for x in v if x >= 0.8) / len(v), 3), "n": len(v)} if (v := by_phase.get(p)) else None)
              for p in V.PHASES}
    active = db.q1("SELECT * FROM vocab_lessons WHERE status='active' ORDER BY created_at DESC LIMIT 1")
    placement = None
    for row in db.q("SELECT * FROM vocab_diagnostics ORDER BY created_at DESC"):
        if json.loads(row["state"]).get("style") == "placement":
            res = json.loads(row["result"]) if row["result"] else None
            placement = {"id": row["id"], "status": row["status"], "completed_at": row["completed_at"]}
            if res:
                # kept apart: words the test actually asked (recorded per word) and a level-based estimate,
                # which is only a number and is never written to any word's state
                placement.update({"tested_words": res["words"], "estimated_known": res.get("known_estimate"),
                                  "estimated_tier": res["estimated_tier"]})
            break
    return {
        "total": len(lib.words),
        "acquired": counts["acquired"], "learning": counts["learning"], "untested": counts["untested"],
        "due": V.due_count(),
        "phases": phases, "phase_labels": V.PHASES,
        "active_lesson": public({**dict(active), "state": json.loads(active["state"]), "result": None}) if active else None,
        "placement": placement,
        "held_audio": len(lib.held_items()),
        "datasets": [{"file": x["file"], "name": x["name"], "license": x["license"], "words": x["words"]}
                     for x in lib.datasets],
        "minutes": list(MINUTES),
    }

