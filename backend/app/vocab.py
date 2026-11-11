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
# placement (the word book's entrance): written words first, across difficulty; a share of the words
# answered correctly are re-checked by ear, and some of those inside a sentence
PLACEMENT_ITEMS = 30
PLACEMENT_EXTRA = 10     # added once when the level is still unsettled
ROUTES = {
    "basic": "文字でも意味が分からない → 意味から覚える",
    "sound": "文字なら分かるが音声で不正解 → 音声付きで覚え直す",
    "context": "単語は聞けるが例文で不正解 → 例文で聞き取る練習",
    "solid": "初見の例文まで理解できた → 復習を減らして次の単語へ",
    "text_only": "文字では分かる（音声は未確認）",
}

STATUS_LABEL = {
    "untested": "未診断",
    "unknown": "意味を覚えていない",
    "sound_gap": "文字では分かるが、音で認識できない",
    "context_gap": "単語単体では聞けるが、文の中で聞き取れない",
    "learning": "学習中",
    "forgotten": "一度覚えたが忘れた（復習間隔を短くして再学習）",
    "confirm": "3つの形式で正解（別の日にもう一度確認すれば習得）",
    "acquired": "習得済み（文字・音声・文脈、別の日にも確認）",
}
CONFIRM_DAYS = 2          # one good day is not "acquired": the word has to come back right on another day
# how a result was obtained; the groups are reported separately and never averaged together
PHASES = {"placement": "語彙診断", "first": "学習直後の確認", "review": "復習（FSRS）",
          "transfer": "初見の例文（応用）", "practice": "練習"}
PROMPT = {
    "text": "この単語の意味は？",
    "audio": "聞こえた単語の意味は？",
    "context": "文の中で使われていた単語の意味は？（選択肢のうち1つだけが文中に出てきます）",
    "transfer": "初めて聞く文です。文の中で使われていた単語の意味は？",
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
    if "lapsed" in row.keys() and row["lapsed"]:
        return "forgotten"
    if t is not None and t < 0.5:
        return "unknown"
    if None not in (t, a, c) and min(t, a, c) >= ACQUIRED:
        days = row["correct_days"] if "correct_days" in row.keys() else CONFIRM_DAYS
        return "acquired" if days >= CONFIRM_DAYS else "confirm"
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
    # forgetting: a modality that had been acquired is now missed; cleared once every tested modality is solid again
    lapsed = row["lapsed"]
    if old is not None and old >= ACQUIRED and score < 0.5:
        lapsed = 1
    elif lapsed:
        others = [row[f"{m}_mastery"] for m in MODALITIES if m != modality and row[f"{m}_mastery"] is not None]
        if new >= ACQUIRED and all(v >= ACQUIRED for v in others):
            lapsed = 0
    today = db.local_date()
    new_day = score >= ACQUIRED and row["last_correct_date"] != today
    conn.execute(f"UPDATE user_vocab SET {modality}_mastery=?, {modality}_n={modality}_n+1, last_tested_at=?, lapsed=?, "
                 "correct_days=correct_days+?, last_correct_date=? WHERE word_id=?",
                 (round(new, 3), now, lapsed, int(new_day), today if score >= ACQUIRED else row["last_correct_date"],
                  word_id))


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
    if ok:
        missed = conn.execute("SELECT 1 FROM attempts WHERE kind='word' AND item_id=? AND score < 0.5 LIMIT 1",
                              (word_id,)).fetchone()
        if missed:
            from . import growth
            w = vocab_library().words[word_id]
            growth.add(conn, "word_in_context", word_id, f"{w.lemma}（{w.meaning}）",
                       "以前は分からなかった単語を、初めて聞く会話の中で聞き取れました")
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
               reason: str | None = None, probe: bool = False, sentence: str = "example",
               phase: str | None = None) -> dict | None:
    """A 4-choice meaning question. `sentence="transfer"` plays the word's second, never-shown sentence
    (a different scene and speaker) instead of the study example."""
    rng = rng or random.Random()
    audio = vocab_library().audio(word)
    if modality == "audio" and "word" not in audio:
        return None
    if modality == "context" and sentence not in audio:
        return None
    text = (word.transfer if sentence == "transfer" else word.example) if modality == "context" else None
    ds = _distractors(word, 3, rng, avoid_text=text)
    if len(ds) < 3:
        return None
    options = [word, *ds]
    rng.shuffle(options)
    public = {"type": "word", "modality": modality, "modality_label": MODALITY_LABEL[modality],
              "prompt": PROMPT["transfer" if sentence == "transfer" and modality == "context" else modality],
              "tier": word.tier, "level": min(5, word.tier + 1), "transfer": sentence == "transfer",
              "choices": [o.meaning for o in options], "review": bool(card), "reason": reason}
    if modality == "text":
        public.update({"lemma": word.lemma, "pos": word.pos})
    else:
        public["audio"] = audio["word"] if modality == "audio" else audio[sentence]
    secret = {"word_id": word.id, "modality": modality, "answer": options.index(word),
              "options": [o.id for o in options], "card": card, "probe": probe, "sentence": sentence,
              "phase": phase or ("review" if card else None)}
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
    phase = sec.get("phase") or {"diag": "placement"}.get(origin, "practice")

    update_mastery(conn, word.id, modality, score)
    inferred = None
    row = conn.execute("SELECT * FROM user_vocab WHERE word_id=?", (word.id,)).fetchone()
    if modality == "audio" and score >= 0.8 and (not row["text_n"] or (row["text_mastery"] or 0) < ACQUIRED):
        # recognising the meaning by ear implies knowing the meaning in writing (also after relearning a word
        # that was missed in writing: word lessons check it by ear, not on paper)
        update_mastery(conn, word.id, "text", score)
        inferred = "text"
    r = None
    if learn:
        # a word just studied in a lesson is enrolled in FSRS even when the first check is right
        r = srs.review(conn, CARD_KIND[modality], word.id, rating_for(score), create=score < 1.0 or bool(sec.get("enroll")))
    _refresh_next_review(conn, word.id)
    conn.execute("INSERT INTO attempts (session_id, created_at, date, kind, block, item_id, level, score, detail)"
                 " VALUES (?,?,?,?,?,?,?,?,?)",
                 (session_id, db.now_iso(), db.local_date(), "word", block or origin, word.id, word.tier, score,
                  json.dumps({"modality": modality, "confidence": confidence, "ms": ms, "choice": choice,
                              "correct": correct, "origin": origin, "inferred": inferred, "phase": phase,
                              "sentence": sec.get("sentence", "example")})))
    row = conn.execute("SELECT * FROM user_vocab WHERE word_id=?", (word.id,)).fetchone()
    audio = lib.audio(word)
    st = status_of(row)
    return {"type": "word", "correct": correct, "answer": sec["answer"], "choice": choice, "score": score, "ms": ms,
            "confidence": confidence, "modality": modality, "lemma": word.lemma, "meaning": word.meaning,
            "pos": word.pos, "example": word.example, "form": word.form, "example_ja": word.example_ja,
            "scene": word.scene, "phase": phase, "phase_label": PHASES.get(phase, phase),
            "sentence": sec.get("sentence", "example"),
            "transfer": word.transfer if sec.get("sentence") == "transfer" else None,
            "transfer_ja": word.transfer_ja if sec.get("sentence") == "transfer" else None,
            "transfer_form": word.transfer_form if sec.get("sentence") == "transfer" else None,
            "transfer_audio": audio.get("transfer") if sec.get("sentence") == "transfer" else None,
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
        return answer_item_conn(conn, item_id, body)


def answer_item_conn(conn, item_id: str, body: dict, session_id: str | None = None, block: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM vocab_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise VocabError(404, "問題が見つかりません")
    payload = json.loads(row["payload"])
    if payload["type"] == "word_study":
        sent = {"recall": body.get("recall") or "no"}
    else:
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
    is_diag = row["origin"] == "diag"
    if payload["type"] == "word_study":
        from . import vocab_lessons
        fb = vocab_lessons.grade_study(conn, payload, body, row["origin_id"])
    else:
        fb = grade(conn, payload, body, origin=row["origin"], session_id=session_id, block=block)
    if is_diag:
        fb = _diag_after(conn, row["origin_id"], payload, fb)
    elif row["origin"] == "learn":
        from . import vocab_lessons
        fb = vocab_lessons.after_answer(conn, row["origin_id"], payload, fb, body)
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


def start_diagnostic(replace: bool = False, max_items: int | None = None, style: str = "listening") -> dict:
    with db.tx() as conn:
        return start_diagnostic_conn(conn, replace, max_items, style)


def _style(row) -> str:
    return json.loads(row["state"]).get("style", "listening")


def start_diagnostic_conn(conn, replace: bool = False, max_items: int | None = None, style: str = "listening") -> dict:
    """style "listening": the listening profile's word part (by ear first, a missed word is asked in writing later).
    style "placement": the word book's entrance (in writing first, words known in writing re-checked by ear)."""
    if max_items is None:
        max_items = PLACEMENT_ITEMS if style == "placement" else DIAG_MAX_ITEMS
    active = [r for r in conn.execute("SELECT id, state FROM vocab_diagnostics WHERE status='active'"
                                      " ORDER BY created_at DESC").fetchall() if _style(r) == style]
    if active and not replace:
        return {**diag_public(_diag_load(conn, active[0]["id"])), "resumed": True}
    for r in active:
        conn.execute("UPDATE vocab_diagnostics SET status='abandoned', updated_at=? WHERE id=?", (db.now_iso(), r["id"]))
        conn.execute("UPDATE vocab_items SET answered=2 WHERE origin='diag' AND origin_id=? AND answered=0", (r["id"],))
    prof = conn.execute("SELECT level FROM profile WHERE id=1").fetchone()
    start = {1: 1, 2: 2, 3: 2, 4: 3, 5: 3}.get(prof["level"], 2)
    did = uuid.uuid4().hex[:12]
    state = {"tier": start, "streak": 0, "asked": [], "queue": [], "history": [], "probes": 0,
             "answered": 0, "max": max_items, "style": style, "extended": False}
    conn.execute("INSERT INTO vocab_diagnostics (id, status, created_at, updated_at, state) VALUES (?,?,?,?,?)",
                 (did, "active", db.now_iso(), db.now_iso(), json.dumps(state)))
    return {**diag_public(_diag_load(conn, did)), "resumed": False}


def diag_next(did: str) -> dict:
    with db.tx() as conn:
        return diag_next_conn(conn, did)


def diag_next_conn(conn, did: str) -> dict:
    lib = vocab_library()
    rng = random.Random()
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
            payload = build_item(lib.words[q["word"]], q["modality"], rng, phase="placement")
            continue
        asked = set(st["asked"])
        fresh = [w for w in lib.by_tier(st["tier"]) if w.id not in asked]
        if not fresh:
            fresh = [w for w in lib.words.values() if w.id not in asked]
        if not fresh:
            break
        w = rng.choice(fresh)
        if st.get("style") == "placement":
            payload = build_item(w, "text", rng, probe=True, phase="placement")
        else:
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
                          "probe": bool(sec.get("probe")), "confidence": fb["confidence"], "ms": fb.get("ms")})
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
    already = {(h["word"], h["modality"]) for h in st["history"]} | {(q["word"], q["modality"]) for q in st["queue"]}
    if st.get("style") == "placement":
        # known in writing -> can it be heard? heard alone -> can it be followed in a sentence?
        # (capped so most of the test still samples new words across levels)
        follow = {"text": "audio", "audio": "context"}.get(sec["modality"]) if score >= 0.8 else None
        cap = {"audio": st["max"] // 3, "context": st["max"] // 6}
        n = sum(1 for h in st["history"] if h["modality"] == follow) + sum(1 for q in st["queue"] if q["modality"] == follow)
        if follow and n < cap[follow] and (word.id, follow) not in already:
            st["queue"].append({"word": word.id, "modality": follow, "after": st["answered"] + FOLLOW_UP_GAP - 1})
    elif score < 0.8:
        # separate "can't hear it" from "doesn't know it": ask the easier form later
        follow = {"context": "audio", "audio": "text"}.get(sec["modality"])
        if follow and (word.id, follow) not in already and st["answered"] + len(st["queue"]) < st["max"]:
            st["queue"].append({"word": word.id, "modality": follow, "after": st["answered"] + FOLLOW_UP_GAP})
    if st["answered"] >= st["max"] and st.get("style") == "placement" and not st.get("extended"):
        # unsettled level: fewer than 4 written probes at the level the staircase ended on -> a few more
        at_tier = sum(1 for h in st["history"] if h["probe"] and h["tier"] == st["tier"])
        if at_tier < 4:
            st["max"] += PLACEMENT_EXTRA
            st["extended"] = True
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
    routes: dict[str, list[str]] = {k: [] for k in ROUTES}
    for w, m in per_word.items():
        t, a, c = m.get("text"), m.get("audio"), m.get("context")
        if (t is not None and t < 0.8) or (t is None and a is not None and a < 0.8 and "text" not in m):
            key = "basic" if t is not None else ("sound" if a is not None else "basic")
        elif a is not None and a < 0.8:
            key = "sound"
        elif c is not None and c < 0.8:
            key = "context"
        elif c is not None:
            key = "solid"
        elif a is not None:
            key = "context"            # heard alone; the sentence check is still to come
        else:
            key = "text_only"
        routes[key].append(w)
    # a rough size of what is known in writing: accuracy of the written probes per level x words in the level
    text_tier: dict[int, list[float]] = {}
    for h in hist:
        if h["probe"] and h["modality"] == "text":
            text_tier.setdefault(h["tier"], []).append(1.0 if h["score"] >= 0.8 else 0.0)
    known_est = None
    if text_tier:
        tested = sorted(text_tier)
        acc = {}
        for t in (1, 2, 3, 4):
            near = min(tested, key=lambda x: (abs(x - t), -x))
            v = text_tier[near]
            a_ = sum(v) / len(v)
            acc[t] = 1.0 if t < tested[0] and a_ >= 0.7 else (0.0 if t > tested[-1] and a_ < 0.3 else a_)
        known_est = int(round(sum(acc[t] * len(lib.by_tier(t)) for t in acc), -1))
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
        "unknown": sum(1 for h in hist if h["confidence"] == "unknown"),
        "style": d["state"].get("style", "listening"),
        "routes": {k: [_word_brief(lib.words[w]) for w in v] for k, v in routes.items()},
        "route_labels": ROUTES,
        "known_estimate": known_est,
        "total_words": len(lib.words),
        "median_ms": _median([h.get("ms") for h in hist if h.get("ms")]),
    }


def _median(xs: list) -> int | None:
    xs = sorted(x for x in xs if x)
    return int(xs[len(xs) // 2]) if xs else None


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
                "style": st.get("style", "listening"),
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
