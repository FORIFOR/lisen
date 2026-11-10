"""Vocabulary engine: adaptive diagnostic, per-modality mastery, FSRS review, item selection.

Every word has three separate abilities, because knowing a word on paper does not mean
hearing it:
  text     – sees the word, knows the meaning
  audio    – hears the word alone, knows the meaning
  context  – hears it inside a sentence, knows the meaning
A word only counts as "acquired" when all three are measured and solid.
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import timedelta

from fsrs import Rating

from . import db, srs
from .vocab_data import Word, overlaps, vocab_library

MODALITIES = ("text", "audio", "context")
MODALITY_LABEL = {"text": "文字で理解", "audio": "音声で認識", "context": "会話で理解"}
CARD_KIND = {"text": "w_text", "audio": "w_audio", "context": "w_ctx"}
KIND_MODALITY = {v: k for k, v in CARD_KIND.items()}
SLOW_MS = {"text": 9000, "audio": 10000, "context": 15000}   # correct but slow -> not yet automatic
ACQUIRED = 0.8
LEARN_RATE = 0.5
DIAG_MAX_ITEMS = 40
FOLLOW_UP_GAP = 3        # ask the follow-up for a word only after a few other questions

STATUS_LABEL = {
    "untested": "未診断",
    "unknown": "意味を覚えていない",
    "sound_gap": "文字では分かるが、音で認識できない",
    "context_gap": "単語単体では聞けるが、文の中で聞き取れない",
    "learning": "学習中",
    "acquired": "習得済み（文字・音声・文脈）",
}
PROMPT = {
    "text": "この単語の意味は？",
    "audio": "聞こえた単語の意味は？",
    "context": "文の中で使われていた単語の意味は？（選択肢のうち1つだけが文中に出てきます）",
}


class VocabError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status
        self.msg = msg


# --- mastery -------------------------------------------------------------------------

def score_answer(correct: bool, confidence: str, ms: int, modality: str) -> float:
    """1.0 sure & quick · 0.8 sure but slow · 0.5 lucky guess · 0 wrong / don't know."""
    if not correct:
        return 0.0
    if confidence == "guess":
        return 0.5
    if ms and ms > SLOW_MS[modality]:
        return 0.8
    return 1.0


def rating_for(score: float) -> Rating:
    if score >= 1.0:
        return Rating.Good
    if score >= 0.5:
        return Rating.Hard
    return Rating.Again


def _masteries(row) -> dict[str, float | None]:
    if not row:
        return {m: None for m in MODALITIES}
    return {m: (row[f"{m}_mastery"] if row[f"{m}_n"] else None) for m in MODALITIES}


def status_of(row) -> str:
    m = _masteries(row)
    t, a, c = m["text"], m["audio"], m["context"]
    if t is None and a is None and c is None:
        return "untested"
    if t is not None and t < 0.5:
        return "unknown"
    if None not in (t, a, c) and min(t, a, c) >= ACQUIRED:
        return "acquired"
    if t is not None and t >= ACQUIRED and a is not None and a < 0.6:
        return "sound_gap"
    if a is not None and a >= ACQUIRED and c is not None and c < 0.6:
        return "context_gap"
    return "learning"


def update_mastery(conn, word_id: str, modality: str, score: float) -> None:
    now = db.now_iso()
    conn.execute("INSERT OR IGNORE INTO user_vocab (word_id, first_seen_at) VALUES (?,?)", (word_id, now))
    row = conn.execute("SELECT * FROM user_vocab WHERE word_id=?", (word_id,)).fetchone()
    old, n = row[f"{modality}_mastery"], row[f"{modality}_n"]
    new = score if not n or old is None else old + LEARN_RATE * (score - old)
    conn.execute(f"UPDATE user_vocab SET {modality}_mastery=?, {modality}_n={modality}_n+1, last_tested_at=? "
                 "WHERE word_id=?", (round(new, 3), now, word_id))


def _refresh_next_review(conn, word_id: str) -> None:
    ids = [srs.card_id(k, word_id) for k in CARD_KIND.values()]
    r = conn.execute(f"SELECT MIN(due) d FROM srs_cards WHERE suspended=0 AND id IN ({','.join('?' * len(ids))})",
                     ids).fetchone()
    conn.execute("UPDATE user_vocab SET next_review_at=? WHERE word_id=?", (r["d"], word_id))


def record_context(conn, word_id: str, ok: bool) -> None:
    """Context evidence from dictation of generated lessons (the word inside natural speech)."""
    if word_id not in vocab_library().words:
        return
    score = 1.0 if ok else 0.0
    update_mastery(conn, word_id, "context", score)
    srs.review(conn, CARD_KIND["context"], word_id, rating_for(score), create=not ok)
    _refresh_next_review(conn, word_id)


# --- items -----------------------------------------------------------------------------

def _distractors(word: Word, n: int, rng: random.Random, avoid_text: str | None = None) -> list[Word]:
    """Wrong options whose meaning cannot also be right (no shared group / overlapping gloss)."""
    lib = vocab_library()
    pool = [w for w in lib.words.values() if not overlaps(word, w)]
    if avoid_text:
        pool = [w for w in pool if not w.appears_in(avoid_text)]
    same_pos = [w for w in pool if w.pos == word.pos]
    cands = same_pos if len(same_pos) >= n + 6 else pool
    cands.sort(key=lambda w: abs(w.tier - word.tier) + rng.random() * 2)
    out: list[Word] = []
    for w in cands:
        if any(overlaps(w, o) for o in out):
            continue
        out.append(w)
        if len(out) == n:
            break
    return out


def build_item(word: Word, modality: str, rng: random.Random | None = None, card: str | None = None,
               reason: str | None = None, probe: bool = False) -> dict | None:
    rng = rng or random.Random()
    audio = vocab_library().audio(word)
    if modality == "audio" and "word" not in audio:
        return None
    if modality == "context" and "example" not in audio:
        return None
    ds = _distractors(word, 3, rng, avoid_text=word.example if modality == "context" else None)
    if len(ds) < 3:
        return None
    options = [word, *ds]
    rng.shuffle(options)
    public = {"type": "word", "modality": modality, "modality_label": MODALITY_LABEL[modality],
              "prompt": PROMPT[modality], "tier": word.tier, "level": min(5, word.tier + 1),
              "choices": [o.meaning for o in options], "review": bool(card), "reason": reason}
    if modality == "text":
        public.update({"lemma": word.lemma, "pos": word.pos})
    else:
        public["audio"] = audio["word"] if modality == "audio" else audio["example"]
    secret = {"word_id": word.id, "modality": modality, "answer": options.index(word),
              "options": [o.id for o in options], "card": card, "probe": probe}
    return {"type": "word", "public": public, "secret": secret}


def grade(conn, payload: dict, body: dict, *, origin: str, session_id: str | None = None,
          block: str | None = None, learn: bool = True) -> dict:
    """Score an answer, update the three masteries and FSRS, and log the attempt."""
    lib = vocab_library()
    sec = payload["secret"]
    word = lib.words[sec["word_id"]]
    modality = sec["modality"]
    choice = body.get("choice")
    confidence = body.get("confidence") or "sure"
    if choice is None:
        confidence = "unknown"
    if confidence not in ("sure", "guess", "unknown"):
        confidence = "sure"
    ms = max(0, min(int(body.get("ms") or 0), 600_000))
    correct = choice is not None and choice == sec["answer"]
    score = score_answer(correct, confidence, ms, modality)

    update_mastery(conn, word.id, modality, score)
    inferred = None
    row = conn.execute("SELECT * FROM user_vocab WHERE word_id=?", (word.id,)).fetchone()
    if modality == "audio" and score >= 0.8 and not row["text_n"]:
        # recognising the meaning by ear implies knowing the meaning in writing
        update_mastery(conn, word.id, "text", score)
        inferred = "text"
    r = None
    if learn:
        r = srs.review(conn, CARD_KIND[modality], word.id, rating_for(score), create=score < 1.0)
    _refresh_next_review(conn, word.id)
    conn.execute("INSERT INTO attempts (session_id, created_at, date, kind, block, item_id, level, score, detail)"
                 " VALUES (?,?,?,?,?,?,?,?,?)",
                 (session_id, db.now_iso(), db.local_date(), "word", block or origin, word.id, word.tier, score,
                  json.dumps({"modality": modality, "confidence": confidence, "ms": ms, "choice": choice,
                              "correct": correct, "origin": origin, "inferred": inferred})))
    row = conn.execute("SELECT * FROM user_vocab WHERE word_id=?", (word.id,)).fetchone()
    audio = lib.audio(word)
    st = status_of(row)
    return {"type": "word", "correct": correct, "answer": sec["answer"], "choice": choice, "score": score,
            "confidence": confidence, "modality": modality, "lemma": word.lemma, "meaning": word.meaning,
            "pos": word.pos, "example": word.example, "form": word.form,
            "word_audio": audio.get("word"), "example_audio": audio.get("example"),
            "mastery": _masteries(row), "status": st, "status_label": STATUS_LABEL[st], "srs": r}


# --- what to practise next ------------------------------------------------------------------

def estimated_tier(conn=None) -> int:
    sql = "SELECT result FROM vocab_diagnostics WHERE status='completed' ORDER BY completed_at DESC LIMIT 1"
    row = conn.execute(sql).fetchone() if conn else db.q1(sql)
    if row and row["result"]:
        return json.loads(row["result"])["estimated_tier"]
    sql = "SELECT level FROM profile WHERE id=1"
    prof = conn.execute(sql).fetchone() if conn else db.q1(sql)
    return {1: 1, 2: 1, 3: 2, 4: 3, 5: 3}.get(prof["level"] if prof else 1, 1)


def _next_modality(row) -> str | None:
    """Move a word on from reading -> hearing it alone -> hearing it in a sentence, once the
    previous ability is solid. Weak abilities are left to FSRS, which already schedules them."""
    m = _masteries(row)
    if m["audio"] is None:
        return "audio" if m["text"] is None or m["text"] >= ACQUIRED else None
    if m["audio"] < ACQUIRED:
        return None
    return "context" if m["context"] is None else None


def pick_next(conn, exclude: set[str], rng: random.Random | None = None, *, due: bool = True,
              advance: bool = True, new: bool = True) -> dict | None:
    rng = rng or random.Random()
    lib = vocab_library()
    if due:
        for c in conn.execute("SELECT * FROM srs_cards WHERE suspended=0 AND kind IN ('w_text','w_audio','w_ctx')"
                              " AND due<? ORDER BY due LIMIT 60", (end_of_today(),)).fetchall():
            if c["ref"] in exclude or c["ref"] not in lib.words:
                continue
            p = build_item(lib.words[c["ref"]], KIND_MODALITY[c["kind"]], rng, card=c["id"], reason="復習（FSRS）")
            if p:
                return p
    if advance:
        rows = conn.execute("SELECT * FROM user_vocab ORDER BY last_tested_at").fetchall()
        for row in rows:
            if row["word_id"] in exclude or row["word_id"] not in lib.words:
                continue
            mod = _next_modality(row)
            if mod:
                p = build_item(lib.words[row["word_id"]], mod, rng,
                               reason="音で確認" if mod == "audio" else "文の中で確認")
                if p:
                    return p
    if new:
        tier = estimated_tier(conn)
        known = {r["word_id"] for r in conn.execute("SELECT word_id FROM user_vocab").fetchall()}
        for t in (tier, min(4, tier + 1), max(1, tier - 1), 1, 2, 3, 4):
            fresh = [w for w in lib.by_tier(t) if w.id not in known and w.id not in exclude]
            if fresh:
                w = rng.choice(fresh)
                return build_item(w, "audio", rng, reason="新しい単語") or build_item(w, "text", rng, reason="新しい単語")
    return None


def card_item(card_id: str, reason: str) -> dict | None:
    kind, ref = card_id.split(":", 1)
    word = vocab_library().words.get(ref)
    if not word or kind not in KIND_MODALITY:
        return None
    return build_item(word, KIND_MODALITY[kind], card=card_id, reason=reason)


def end_of_today() -> str:
    """Word reviews are "today's" reviews: cards due any time before local midnight
    (a word missed a minute ago is due again within minutes)."""
    local = db.now().astimezone()
    return (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()


def due_count(conn=None) -> int:
    sql = "SELECT COUNT(*) n FROM srs_cards WHERE suspended=0 AND kind IN ('w_text','w_audio','w_ctx') AND due<?"
    return (conn.execute(sql, (end_of_today(),)).fetchone() if conn else db.q1(sql, (end_of_today(),)))["n"]


# --- stored items (diagnostic / standalone review) -------------------------------------------

def _insert_item(conn, origin: str, origin_id: str | None, payload: dict) -> dict:
    iid = uuid.uuid4().hex[:12]
    payload["public"]["item_id"] = iid
    conn.execute("INSERT INTO vocab_items (id, origin, origin_id, created_at, payload) VALUES (?,?,?,?,?)",
                 (iid, origin, origin_id, db.now_iso(), json.dumps(payload, ensure_ascii=False)))
    return payload["public"]


def answer_item(item_id: str, body: dict) -> dict:
    """Grade a stored item. Re-sending the same answer returns the stored result (safe retry);
    a different answer to an already-answered item is rejected."""
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM vocab_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise VocabError(404, "問題が見つかりません")
        sent = {"choice": body.get("choice"), "confidence": body.get("confidence") or "sure"}
        if sent["choice"] is None:
            sent["confidence"] = "unknown"
        if row["answered"] == 1:
            prev = json.loads(row["answer"])
            if prev == sent:
                return {**json.loads(row["feedback"]), "duplicate": True}
            raise VocabError(409, "この問題は回答済みです（別の端末で回答された可能性があります）")
        if row["answered"] == 2:
            raise VocabError(409, "この問題は無効になりました（診断がやり直されました）")
        payload = json.loads(row["payload"])
        is_diag = row["origin"] == "diag"
        fb = grade(conn, payload, body, origin=row["origin"])
        if is_diag:
            fb = _diag_after(conn, row["origin_id"], payload, fb)
        conn.execute("UPDATE vocab_items SET answered=1, answer=?, feedback=? WHERE id=?",
                     (json.dumps(sent), json.dumps(fb, ensure_ascii=False), item_id))
    return fb


def review_items(limit: int = 20, include_new: bool = False) -> dict:
    """Today's word reviews (FSRS due). With include_new, tops up with next-step and new words."""
    origin = "practice" if include_new else "review"
    rng = random.Random()
    with db.tx() as conn:
        stale = (db.now() - timedelta(hours=12)).isoformat()
        conn.execute("UPDATE vocab_items SET answered=2 WHERE origin=? AND answered=0 AND created_at<?", (origin, stale))
        items = [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM vocab_items WHERE origin=? AND answered=0 ORDER BY created_at", (origin,)).fetchall()]
        exclude = {p["secret"]["word_id"] for p in items}
        out = [p["public"] for p in items][:limit]
        while len(out) < limit:
            p = pick_next(conn, exclude, rng, due=True, advance=include_new, new=include_new)
            if not p:
                break
            exclude.add(p["secret"]["word_id"])
            out.append(_insert_item(conn, origin, None, p))
        n_due = due_count(conn)
    return {"due": n_due, "items": out}


# --- adaptive diagnostic ------------------------------------------------------------------

def _diag_load(conn, did: str) -> dict:
    row = conn.execute("SELECT * FROM vocab_diagnostics WHERE id=?", (did,)).fetchone()
    if not row:
        raise VocabError(404, "診断が見つかりません")
    d = dict(row)
    d["state"] = json.loads(d["state"])
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d


def _diag_save(conn, d: dict) -> None:
    conn.execute("UPDATE vocab_diagnostics SET status=?, updated_at=?, completed_at=?, state=?, result=? WHERE id=?",
                 (d["status"], db.now_iso(), d.get("completed_at"), json.dumps(d["state"]),
                  json.dumps(d["result"], ensure_ascii=False) if d["result"] else None, d["id"]))


def diag_public(d: dict) -> dict:
    st = d["state"]
    return {"id": d["id"], "status": d["status"], "answered": st["answered"], "max": st["max"],
            "tier": st["tier"], "created_at": d["created_at"], "result": d["result"]}


def start_diagnostic(replace: bool = False) -> dict:
    with db.tx() as conn:
        active = conn.execute("SELECT id FROM vocab_diagnostics WHERE status='active' ORDER BY created_at DESC").fetchall()
        if active and not replace:
            return {**diag_public(_diag_load(conn, active[0]["id"])), "resumed": True}
        for r in active:
            conn.execute("UPDATE vocab_diagnostics SET status='abandoned', updated_at=? WHERE id=?", (db.now_iso(), r["id"]))
            conn.execute("UPDATE vocab_items SET answered=2 WHERE origin='diag' AND origin_id=? AND answered=0", (r["id"],))
        prof = conn.execute("SELECT level FROM profile WHERE id=1").fetchone()
        start = {1: 1, 2: 2, 3: 2, 4: 3, 5: 3}.get(prof["level"], 2)
        did = uuid.uuid4().hex[:12]
        state = {"tier": start, "streak": 0, "asked": [], "queue": [], "history": [], "probes": 0,
                 "answered": 0, "max": DIAG_MAX_ITEMS}
        conn.execute("INSERT INTO vocab_diagnostics (id, status, created_at, updated_at, state) VALUES (?,?,?,?,?)",
                     (did, "active", db.now_iso(), db.now_iso(), json.dumps(state)))
        return {**diag_public(_diag_load(conn, did)), "resumed": False}


def diag_next(did: str) -> dict:
    lib = vocab_library()
    rng = random.Random()
    with db.tx() as conn:
        d = _diag_load(conn, did)
        if d["status"] != "active":
            return {"diagnostic": diag_public(d), "item": None, "done": d["status"] == "completed"}
        pending = conn.execute("SELECT payload FROM vocab_items WHERE origin='diag' AND origin_id=? AND answered=0"
                               " ORDER BY created_at DESC LIMIT 1", (did,)).fetchone()
        if pending:
            return {"diagnostic": diag_public(d), "item": json.loads(pending["payload"])["public"], "done": False}
        st = d["state"]
        payload = None
        while payload is None and st["answered"] < st["max"]:
            ready = [q for q in st["queue"] if q["after"] <= st["answered"]]
            if ready:
                q = ready[0]
                st["queue"].remove(q)
                payload = build_item(lib.words[q["word"]], q["modality"], rng)
                continue
            asked = set(st["asked"])
            fresh = [w for w in lib.by_tier(st["tier"]) if w.id not in asked]
            if not fresh:
                fresh = [w for w in lib.words.values() if w.id not in asked]
            if not fresh:
                break
            w = rng.choice(fresh)
            mod = "context" if st["probes"] % 4 == 3 else "audio"
            payload = build_item(w, mod, rng, probe=True) or build_item(w, "text", rng, probe=True)
            st["asked"].append(w.id)
            st["probes"] += 1
        if payload is None:
            _diag_complete(conn, d)
            _diag_save(conn, d)
            return {"diagnostic": diag_public(d), "item": None, "done": True}
        payload["public"]["progress"] = {"answered": st["answered"], "max": st["max"]}
        item = _insert_item(conn, "diag", did, payload)
        _diag_save(conn, d)
        return {"diagnostic": diag_public(d), "item": item, "done": False}


def _diag_after(conn, did: str, payload: dict, fb: dict) -> dict:
    d = _diag_load(conn, did)
    st = d["state"]
    sec = payload["secret"]
    word = vocab_library().words[sec["word_id"]]
    score = fb["score"]
    st["answered"] += 1
    st["history"].append({"word": word.id, "tier": word.tier, "modality": sec["modality"], "score": score,
                          "probe": bool(sec.get("probe")), "confidence": fb["confidence"]})
    if sec.get("probe"):
        # 2-up / 1-down staircase: converges near 70% correct
        if score >= 0.8:
            st["streak"] += 1
            if st["streak"] >= 2:
                st["tier"] = min(4, st["tier"] + 1)
                st["streak"] = 0
        else:
            st["streak"] = 0
            st["tier"] = max(1, st["tier"] - 1)
    if score < 0.8:
        # separate "can't hear it" from "doesn't know it": ask the easier form later
        follow = {"context": "audio", "audio": "text"}.get(sec["modality"])
        already = {(h["word"], h["modality"]) for h in st["history"]} | {(q["word"], q["modality"]) for q in st["queue"]}
        if follow and (word.id, follow) not in already and st["answered"] + len(st["queue"]) < st["max"]:
            st["queue"].append({"word": word.id, "modality": follow, "after": st["answered"] + FOLLOW_UP_GAP})
    if st["answered"] >= st["max"]:
        _diag_complete(conn, d)
    _diag_save(conn, d)
    # during the diagnostic the answer is not revealed: a later follow-up asks about the same word
    out = {"type": "word", "recorded": True, "diagnostic": diag_public(d), "done": d["status"] == "completed",
           "confidence": fb["confidence"], "score": fb["score"]}
    return out


def _diag_complete(conn, d: dict) -> None:
    lib = vocab_library()
    hist = d["state"]["history"]
    by_mod: dict[str, list[float]] = {}
    by_tier: dict[int, list[float]] = {}
    per_word: dict[str, dict[str, float]] = {}
    for h in hist:
        by_mod.setdefault(h["modality"], []).append(h["score"])
        if h["probe"]:
            by_tier.setdefault(h["tier"], []).append(h["score"])
        per_word.setdefault(h["word"], {})[h["modality"]] = h["score"]
    estimate = 1
    for t in (1, 2, 3, 4):
        s = by_tier.get(t, [])
        if len(s) >= 3 and sum(1 for x in s if x >= 0.8) / len(s) >= 0.7:
            estimate = t
    sound_gap = [w for w, m in per_word.items() if m.get("text", 0) >= 0.8 and m.get("audio", 1) < 0.8]
    weak = [w for w, m in per_word.items() if min(m.values()) < 0.8]
    weak.sort(key=lambda w: (min(per_word[w].values()), lib.words[w].tier))
    d["status"] = "completed"
    d["completed_at"] = db.now_iso()
    d["result"] = {
        "estimated_tier": estimate,
        "items": len(hist),
        "words": len(per_word),
        "modality": {m: {"accuracy": round(sum(v) / len(v), 3), "n": len(v)} for m, v in by_mod.items()},
        "tiers": {str(t): {"accuracy": round(sum(1 for x in v if x >= 0.8) / len(v), 3), "n": len(v)}
                  for t, v in sorted(by_tier.items())},
        "sound_gap": [_word_brief(lib.words[w]) for w in sound_gap],
        "weak": [{**_word_brief(lib.words[w]), "failed": [m for m, s in per_word[w].items() if s < 0.8]} for w in weak],
        "guesses": sum(1 for h in hist if h["confidence"] == "guess"),
    }


def _word_brief(w: Word) -> dict:
    a = vocab_library().audio(w)
    return {"id": w.id, "lemma": w.lemma, "meaning": w.meaning, "tier": w.tier, "audio": a.get("word")}


# --- stats -----------------------------------------------------------------------------------

def stats() -> dict:
    lib = vocab_library()
    rows = {r["word_id"]: r for r in db.q("SELECT * FROM user_vocab")}
    statuses: dict[str, int] = {k: 0 for k in STATUS_LABEL}
    for wid in lib.words:
        statuses[status_of(rows.get(wid))] += 1
    modality = {}
    for m in MODALITIES:
        vals = [r[f"{m}_mastery"] for r in rows.values() if r[f"{m}_n"]]
        modality[m] = {"label": MODALITY_LABEL[m], "tested": len(vals),
                       "mastery": round(sum(vals) / len(vals), 3) if vals else None}
    tiers = []
    for t in (1, 2, 3, 4):
        ws = lib.by_tier(t)
        tested = [rows[w.id] for w in ws if w.id in rows]
        tiers.append({"tier": t, "total": len(ws), "tested": len(tested),
                      "acquired": sum(1 for r in tested if status_of(r) == "acquired"),
                      **{m: (round(sum(r[f"{m}_mastery"] for r in tested if r[f"{m}_n"]) /
                                   max(1, sum(1 for r in tested if r[f"{m}_n"])), 3)
                             if any(r[f"{m}_n"] for r in tested) else None) for m in MODALITIES}})
    focus = []
    for wid, r in rows.items():
        if wid not in lib.words:
            continue
        st = status_of(r)
        if st in ("acquired", "untested"):
            continue
        m = _masteries(r)
        # ties go to the most basic ability (not knowing the meaning comes before not hearing it)
        weakest = min((v, MODALITIES.index(k), k) for k, v in m.items() if v is not None)
        weakest = (weakest[0], weakest[2])
        focus.append((weakest[0], r["next_review_at"] or "", wid, weakest[1], st, m))
    focus.sort()
    due_words = due_count()
    active = db.q1("SELECT * FROM vocab_diagnostics WHERE status='active' ORDER BY created_at DESC LIMIT 1")
    last = db.q1("SELECT * FROM vocab_diagnostics WHERE status='completed' ORDER BY completed_at DESC LIMIT 1")

    def diag(row):
        if not row:
            return None
        st = json.loads(row["state"])
        return {"id": row["id"], "answered": st["answered"], "max": st["max"], "completed_at": row["completed_at"],
                "result": json.loads(row["result"]) if row["result"] else None}

    return {
        "dataset": {"name": lib.meta["name"], "license": lib.meta["license"], "words": len(lib.words)},
        "tested_words": sum(1 for r in rows.values() if any(r[f"{m}_n"] for m in MODALITIES)),
        "acquired": statuses["acquired"],
        "due": due_words,
        "status": [{"status": k, "label": STATUS_LABEL[k], "count": v} for k, v in statuses.items()],
        "modality": modality,
        "tiers": tiers,
        "estimated_tier": estimated_tier(),
        "focus": [{**_word_brief(lib.words[wid]), "weakest": mod, "weakest_label": MODALITY_LABEL[mod],
                   "status": st, "status_label": STATUS_LABEL[st], "mastery": m, "next_review_at": nr or None}
                  for _, nr, wid, mod, st, m in focus[:8]],
        "diagnostic": {"active": diag(active), "last": diag(last)},
    }
