"""Training sessions: item selection per block, answer evaluation, time accounting.

The client only decides how long to study; everything else (what to listen to next,
when to move on to new audio, what to review) is decided here.
"""
from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timedelta

from fsrs import Rating

from . import abilities, db, planner, progress, sentences, srs, study, vocab
from .content import Clip, Segment, library
from .scoring import SKILLS, is_typo, make_discrimination, score_dictation, tokenize

MAX_ELAPSED_REPORT = 120     # seconds accepted per time report (guards against sleeping tabs)
MOVE_ON_ACCURACY = 0.9       # two consecutive segments at this accuracy -> new audio
MOVE_ON_STREAK = 2
# blocks that run the Context -> Sentence -> Transfer cycle
CYCLE_BLOCKS = ("new", "multi", "long", "commute")
# commute mode: what a tap on one line says
LINE_REPORTS = ("understood", "sound_only", "not_heard")
# listening-profile diagnostic: blocks that end after a number of items instead of a time budget
DIAG_ITEMS = {"d_sound": 10, "d_sentence": 10}
DIAG_WORDS = 20
SOUND_FOCUS = ["weak_forms", "linking", "contractions", "endings", "numbers"]


class SessionError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status
        self.msg = msg


# --- session lifecycle -------------------------------------------------------

def _load(session_id: str, conn=None) -> dict:
    row = (conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone() if conn
           else db.q1("SELECT * FROM sessions WHERE id=?", (session_id,)))
    if not row:
        raise SessionError(404, "セッションが見つかりません")
    s = dict(row)
    s["plan"] = json.loads(s["plan"])
    s["state"] = json.loads(s["state"])
    return s


def _save(conn, s: dict) -> None:
    conn.execute("UPDATE sessions SET plan=?, state=?, status=?, updated_at=?, completed_at=? WHERE id=?",
                 (json.dumps(s["plan"]), json.dumps(s["state"]), s["status"], db.now_iso(), s.get("completed_at"),
                  s["id"]))


def _new_state(n_blocks: int) -> dict:
    return {"block_index": 0, "block_seconds": [0.0] * n_blocks, "study_seconds": 0.0,
            "shown": {"segment": [], "clip": [], "vocab": [], "card": [], "shadow": [], "word": []},
            "cycle": None, "missed_cards": [], "final_done": [], "counter": 0,
            "test_queue": None, "events": [], "notes": [], "block_items": [0] * n_blocks}


def active_session() -> dict | None:
    row = db.q1("SELECT id FROM sessions WHERE status IN ('active','paused') ORDER BY updated_at DESC LIMIT 1")
    return public_session(_load(row["id"])) if row else None


def create_session(minutes: int, mode: str = "standard", replace: bool = False, form: str | None = None,
                   focus_clip: str | None = None) -> dict:
    prof = db.profile()
    if mode == "test":
        if form not in progress.FORMS:
            raise SessionError(400, "不明なテストです")
        plan = [planner._block("test", 0)]
    elif mode == "profile":
        # listening-profile diagnostic: sound -> words -> sentences -> unseen conversations (~20-25 min)
        plan = [planner._block(k, m) for k, m in (("d_sound", 4), ("d_words", 5), ("d_sentence", 4), ("test", 10))]
        taken = {r["form"] for r in db.q("SELECT form FROM tests")}
        first = progress.form_for_slot(1)          # the learner's (randomly assigned) first checkpoint form
        form = first if first not in taken else None
    else:
        if not 5 <= minutes <= 300:
            raise SessionError(400, "学習時間は 5〜300 分で指定してください")
        plan = planner.build_plan(minutes, prof["settings"], mode, abilities.current())
    with db.tx() as conn:
        cur = conn.execute("SELECT id FROM sessions WHERE status IN ('active','paused')").fetchall()
        if cur and not replace:
            raise SessionError(409, "進行中のセッションがあります")
        for r in cur:
            conn.execute("UPDATE sessions SET status='abandoned', updated_at=? WHERE id=?", (db.now_iso(), r["id"]))
        sid = uuid.uuid4().hex[:12]
        state = _new_state(len(plan))
        if focus_clip and focus_clip in library().clips:
            state["focus_clip"] = focus_clip
        if mode == "test":
            state["test_queue"] = _test_queue(form)
        elif mode == "profile":
            state["test_queue"] = _test_queue(form) if form else _unseen_queue(conn, prof["level"])
        conn.execute("INSERT INTO sessions (id, mode, minutes, status, created_at, updated_at, plan, state, test_form)"
                     " VALUES (?,?,?,?,?,?,?,?,?)",
                     (sid, mode, minutes, "active", db.now_iso(), db.now_iso(), json.dumps(plan), json.dumps(state), form))
    return public_session(_load(sid))


def public_session(s: dict) -> dict:
    st = s["state"]
    blocks = []
    for i, b in enumerate(s["plan"]):
        blocks.append({**b, "elapsed": round(st["block_seconds"][i]), "done": i < st["block_index"],
                       "current": i == st["block_index"] and s["status"] != "completed"})
    return {"id": s["id"], "mode": s["mode"], "minutes": s["minutes"], "status": s["status"],
            "test_form": s.get("test_form"), "created_at": s["created_at"], "blocks": blocks,
            "block_index": st["block_index"], "study_seconds": round(st["study_seconds"]),
            "planned_study_minutes": planner.study_minutes(s["plan"]), "notes": st.get("notes", [])[-3:],
            "block_items": st.get("block_items", []), "diag_items": {**DIAG_ITEMS, "d_words": DIAG_WORDS}}


def _account_time(conn, s: dict, elapsed: float | None) -> None:
    if not elapsed or elapsed <= 0 or s["status"] == "completed":
        return
    elapsed = min(float(elapsed), MAX_ELAPSED_REPORT)
    st = s["state"]
    idx = min(st["block_index"], len(s["plan"]) - 1)
    st["block_seconds"][idx] += elapsed
    if s["plan"][idx]["kind"] != "break":
        st["study_seconds"] += elapsed
        conn.execute("INSERT INTO daily_log (date, seconds) VALUES (?, ?) "
                     "ON CONFLICT(date) DO UPDATE SET seconds=seconds+excluded.seconds",
                     (db.local_date(), int(round(elapsed))))


def heartbeat(session_id: str, elapsed: float | None, status: str | None = None) -> dict:
    with db.tx() as conn:
        s = _load(session_id, conn)
        if s["status"] in ("completed", "abandoned"):
            return public_session(s)
        _account_time(conn, s, elapsed)
        if status in ("active", "paused"):
            s["status"] = status
        _save(conn, s)
    return public_session(s)


def extend(session_id: str, minutes: int) -> dict:
    if not 5 <= minutes <= 120:
        raise SessionError(400, "延長は 5〜120 分で指定してください")
    with db.tx() as conn:
        s = _load(session_id, conn)
        if s["status"] in ("completed", "abandoned"):
            raise SessionError(409, "このセッションは終了しています")
        if s["mode"] == "commute":
            extra = [planner._block("commute", minutes)]
        else:
            extra = planner.extra_blocks(minutes, start=s["state"]["counter"],
                                         first=abilities.BLOCK_FOR.get(abilities.priority(abilities.current(conn)) or ""))
        plan = s["plan"]
        idx = s["state"]["block_index"]
        # put extension before an unstarted closing block (shadowing / final review)
        insert_at = len(plan)
        while insert_at - 1 > idx and plan[insert_at - 1]["kind"] in ("shadow", "final"):
            insert_at -= 1
        s["plan"] = plan[:insert_at] + extra + plan[insert_at:]
        bs = s["state"]["block_seconds"]
        s["state"]["block_seconds"] = bs[:insert_at] + [0.0] * len(extra) + bs[insert_at:]
        bit = s["state"].setdefault("block_items", [0] * len(bs))
        s["state"]["block_items"] = bit[:insert_at] + [0] * len(extra) + bit[insert_at:]
        s["minutes"] = s["minutes"] + minutes
        conn.execute("UPDATE sessions SET minutes=? WHERE id=?", (s["minutes"], session_id))
        _save(conn, s)
    return public_session(s)


def skip_block(session_id: str) -> dict:
    with db.tx() as conn:
        s = _load(session_id, conn)
        _advance(conn, s)
        _save(conn, s)
    return public_session(s)


def finish(session_id: str) -> dict:
    with db.tx() as conn:
        s = _load(session_id, conn)
        if s["status"] != "completed":
            s["status"] = "completed"
            s["completed_at"] = db.now_iso()
            conn.execute("UPDATE pending_items SET answered=2 WHERE session_id=? AND answered=0", (session_id,))
            _on_complete(conn, s)
            _save(conn, s)
    return summary(session_id)


def _advance(conn, s: dict) -> None:
    st = s["state"]
    st["block_index"] += 1
    st["cycle"] = None
    conn.execute("UPDATE pending_items SET answered=2 WHERE session_id=? AND answered=0", (s["id"],))
    if st["block_index"] >= len(s["plan"]):
        s["status"] = "completed"
        s["completed_at"] = db.now_iso()
        _on_complete(conn, s)


# --- next item ---------------------------------------------------------------

def next_item(session_id: str, elapsed: float | None = None) -> dict:
    with db.tx() as conn:
        s = _load(session_id, conn)
        if s["status"] == "abandoned":
            raise SessionError(409, "このセッションは破棄されています")
        _account_time(conn, s, elapsed)
        if s["status"] == "paused":
            s["status"] = "active"
        result = None
        while s["status"] != "completed":
            pending = conn.execute("SELECT * FROM pending_items WHERE session_id=? AND answered=0 ORDER BY created_at DESC LIMIT 1",
                                   (session_id,)).fetchone()
            if pending:
                result = {"type": "item", "item": json.loads(pending["payload"])["public"]}
                break
            st = s["state"]
            block = s["plan"][st["block_index"]]
            limit = block["minutes"] * 60
            if block["kind"] == "break":
                if st["block_seconds"][st["block_index"]] >= limit:
                    _advance(conn, s)
                    continue
                result = {"type": "break", "seconds_left": max(0, round(limit - st["block_seconds"][st["block_index"]]))}
                break
            bi = st["block_index"]
            st.setdefault("block_items", [0] * len(s["plan"]))
            if block["kind"] in DIAG_ITEMS:
                if st["block_items"][bi] >= DIAG_ITEMS[block["kind"]]:
                    _advance(conn, s)
                    continue
            elif block["kind"] not in ("test", "d_words") and st["block_seconds"][bi] >= limit \
                    and not _cycle_open(st, bi):
                # (a cycle already at its study or transfer step is finished first: cutting it there would
                # leave a missed sentence unexplained)
                _advance(conn, s)
                continue
            payload = _select(conn, s, block)
            if payload is None:
                note = f"「{block['label']}」に出せる教材がなくなったため次へ進みました"
                if block["kind"] != "test" and not block["kind"].startswith("d_"):
                    st["notes"].append(note)
                _advance(conn, s)
                continue
            pid = uuid.uuid4().hex[:12]
            payload["public"]["item_id"] = pid
            payload["public"]["block"] = block["kind"]
            conn.execute("INSERT INTO pending_items (id, session_id, created_at, payload) VALUES (?,?,?,?)",
                         (pid, session_id, db.now_iso(), json.dumps(payload)))
            st["counter"] += 1
            st["block_items"][bi] += 1
            result = {"type": "item", "item": payload["public"]}
            break
        _save(conn, s)
    if s["status"] == "completed":
        return {"type": "done", "session": public_session(s)}
    result["session"] = public_session(s)
    return result


def _exposure_counts(conn, kind: str) -> dict[str, dict]:
    return {r["item_id"]: dict(r) for r in conn.execute("SELECT * FROM exposures WHERE kind=?", (kind,)).fetchall()}


def _level_order(level: int) -> list[int]:
    """Current level first, then easier, then one level up as a stretch."""
    return [lv for lv in (level, level - 1, level + 1, level - 2, level - 3, level - 4) if 1 <= lv <= 5]


def _pick_new_clip(conn, s: dict, kind: str, prof: dict) -> tuple[Clip, bool] | None:
    lib = library()
    seen = _exposure_counts(conn, "clip")
    shown = set(s["state"]["shown"]["clip"])
    focus = s["state"].get("focus_clip")
    if focus and kind == "new" and focus not in shown and focus in lib.clips:
        return lib.clips[focus], focus in seen          # chosen from the home screen
    clips = lib.train_clips()
    if kind == "multi":
        clips = [c for c in clips if c.speakers >= 2]
    elif kind == "long":
        clips = [c for c in clips if len(c.segments) >= 4 and c.level >= max(1, prof["level"] - 1)]
        clips.sort(key=lambda c: -c.duration)
    for lv in _level_order(prof["level"]):
        fresh = [c for c in clips if c.level == lv and c.id not in seen and c.id not in shown]
        if fresh:
            if kind == "long":
                return fresh[0], False
            # lessons built from the learner's weak words, then user-added material, then the learner's
            # interests, then random
            raw = prof.get("settings") or "{}"
            interests = (json.loads(raw) if isinstance(raw, str) else raw).get("interests") or []
            fresh.sort(key=lambda c: ({"lesson": 0, "user": 1}.get(c.source, 2), c.topic not in interests, random.random()))
            return fresh[0], False
    # every clip has been heard: re-listen to the one heard longest ago (never twice in one session)
    old = [c for c in clips if c.id not in shown and abs(c.level - prof["level"]) <= 1]
    if not old:
        return None
    old.sort(key=lambda c: seen.get(c.id, {}).get("last_seen") or "")
    return old[0], True


def _comprehension_payload(clip: Clip, relisten: bool, block: str) -> dict:
    # choices are shuffled every time: the written material puts most right answers second, and a fixed
    # position would let "always pick 2" score ~80% on the comprehension measures
    rng = random.Random()
    public_qs, answers = [], []
    for q in clip.questions:
        order = list(range(len(q["choices"])))
        rng.shuffle(order)
        public_qs.append({"q": q["q"], "choices": [q["choices"][i] for i in order], "type": q.get("type")})
        answers.append(order.index(q["answer"]))
    return {
        "type": "comprehension",
        "public": {"type": "comprehension", "clip": clip.public(with_text=False), "title": clip.title,
                   "level": clip.level, "relisten": relisten, "questions": public_qs},
        "secret": {"clip_id": clip.id, "answers": answers},
    }


def _dictation_payload(seg: Segment, review_card: str | None = None, reason: str | None = None) -> dict:
    clip = library().clips[seg.clip_id]
    return {
        "type": "dictation",
        "public": {"type": "dictation", "segment": {"id": seg.id, "audio": seg.audio, "speaker": seg.speaker,
                                                     "speakers": clip.speakers, "index": seg.index},
                   "clip_title": clip.title, "level": seg.level, "word_count": len(seg.text.split()),
                   "review": bool(review_card), "reason": reason,
                   "context": [{"speaker": x.speaker, "text": x.text} for x in clip.segments[max(0, seg.index - 1):seg.index]]},
        "secret": {"segment_id": seg.id, "card": review_card},
    }


def _discrimination_payload(seg: Segment, prefer: str | None) -> dict | None:
    d = make_discrimination(seg.text, prefer)
    if not d:
        return None
    clip = library().clips[seg.clip_id]
    return {
        "type": "discrimination",
        "public": {"type": "discrimination", "segment": {"id": seg.id, "audio": seg.audio, "speaker": seg.speaker},
                   "clip_title": clip.title, "level": seg.level, "options": d["options"],
                   "focus": SKILLS.get(d["category"], d["category"])},
        "secret": {"segment_id": seg.id, **d},
    }


def _mask(text: str, word: str) -> str:
    return re.sub(rf"\b{re.escape(word)}\b", "＿" * max(3, len(word) // 2), text, count=1, flags=re.I)


def _vocab_payload(v: dict, review_card: str | None = None) -> dict | None:
    if not v.get("segment_id"):
        return None
    seg = library().segments[v["segment_id"]]
    return {
        "type": "vocab",
        "public": {"type": "vocab", "segment": {"id": seg.id, "audio": seg.audio, "speaker": seg.speaker},
                   "masked": _mask(seg.text, v["word"]), "letters": len(v["word"].replace(" ", "")),
                   "first_letter": v["word"][0], "level": v["level"], "review": bool(review_card)},
        "secret": {"vocab_id": v["id"], "card": review_card},
    }


def _shadow_payload(seg: Segment) -> dict:
    clip = library().clips[seg.clip_id]
    return {"type": "shadowing",
            "public": {"type": "shadowing", "segment": {"id": seg.id, "audio": seg.audio, "speaker": seg.speaker},
                       "text": seg.text, "clip_title": clip.title, "level": seg.level},
            "secret": {"segment_id": seg.id}}


def _choose_weak_skill(prof: dict) -> str:
    m = progress.mastery()
    focus = set(progress.phase()["focus"])
    scored = []
    for k, v in m.items():
        if k == "comprehension":
            continue
        score = v["mastery"] - (0.08 if k in focus else 0) + random.uniform(0, 0.04)
        scored.append((score, k))
    return min(scored)[1]


def _segment_pool(conn, s: dict, prof: dict, skill: str | None = None, lv_range: tuple[int, int] | None = None,
                  exclude_shown: str = "segment") -> list[Segment]:
    lib = library()
    lo, hi = lv_range or (max(1, prof["level"] - 1), prof["level"])
    shown = set(s["state"]["shown"][exclude_shown])
    seen = _exposure_counts(conn, "segment")
    # material queued as the learner's next first listen (generated lessons, own material) stays unheard
    heard = _exposure_counts(conn, "clip")
    keep_fresh = {c.id for c in lib.train_clips() if c.source in ("lesson", "user") and c.id not in heard
                  and c.id not in s["state"]["shown"]["clip"]}
    pool = [seg for c in lib.train_clips() if c.id not in keep_fresh
            for seg in c.segments if lo <= seg.level <= hi and seg.id not in shown]
    if skill:
        def key(seg: Segment):
            exp = seen.get(seg.id, {}).get("count", 0)
            return -(seg.skill_tokens.get(skill, 0) * 1.0 - 0.7 * exp) + random.uniform(0, 0.8)
        pool.sort(key=key)
    else:
        random.shuffle(pool)
    return pool


def _due_card_payload(conn, s: dict) -> dict | None:
    lib = library()
    shown_cards = set(s["state"]["shown"]["card"])
    for card in srs.due_cards(limit=30, exclude=shown_cards):
        if card["kind"] == "seg" and card["ref"] in lib.segments:
            s["state"]["shown"]["card"].append(card["id"])
            s["state"]["shown"]["segment"].append(card["ref"])
            return _dictation_payload(lib.segments[card["ref"]], review_card=card["id"], reason="復習（FSRS）")
        if card["kind"] == "vocab" and card["ref"] in lib.vocab:
            p = _vocab_payload(lib.vocab[card["ref"]], review_card=card["id"])
            if p:
                s["state"]["shown"]["card"].append(card["id"])
                return p
        if card["kind"] == "sent" and card["ref"] in sentences.bank().items:
            done = s["state"]["shown"].setdefault("sentence", [])
            if card["ref"] in done:
                continue
            done.append(card["ref"])
            s["state"]["shown"]["card"].append(card["id"])
            return sentences.item(sentences.bank().items[card["ref"]], reason="復習（FSRS）")
        if card["kind"] in vocab.KIND_MODALITY:
            shown_words = s["state"]["shown"].setdefault("word", [])
            if card["ref"] in shown_words:
                continue
            p = vocab.card_item(card["id"], "復習（FSRS）")
            if p:
                s["state"]["shown"]["card"].append(card["id"])
                shown_words.append(card["ref"])
                return p
    return None


def _cycle_lines(clip: Clip, shown: list[str]) -> list[str]:
    """Lines to dictate in search of the one not heard. In a generated lesson the lines with its target words
    come first: those words are why the lesson exists."""
    segs = [sg for sg in clip.segments if sg.id not in shown]
    if clip.targets:
        from .vocab_data import vocab_library
        words = [w for w in (vocab_library().words.get(t) for t in clip.targets) if w]
        segs.sort(key=lambda sg: not any(w.appears_in(sg.text) for w in words))
    return [sg.id for sg in segs]


def _cycle_open(st: dict, bi: int) -> bool:
    cyc = st.get("cycle")
    return bool(cyc) and cyc.get("block") == bi and cyc.get("stage") in ("study", "transfer")


def _line_check_payload(seg: Segment, index: int, total: int) -> dict:
    """Commute mode: hear one line and tap whether it was heard (no typing)."""
    return {"type": "line_check",
            "public": {"type": "line_check", "segment": {"id": seg.id, "audio": seg.audio, "speaker": seg.speaker},
                       "level": seg.level, "index": index, "total": total, "reason": "聞き取れなかった1文を探す"},
            "secret": {"segment_id": seg.id}}


def _cycle_item(conn, s: dict, cyc: dict) -> dict | None:
    """The next step of the Context -> Sentence -> Transfer cycle, or None when it is over."""
    lib = library()
    st = s["state"]
    if cyc["stage"] == "lines":
        while cyc["segments"]:
            sid = cyc["segments"].pop(0)
            if sid in lib.segments:
                st["shown"]["segment"].append(sid)
                if cyc.get("tap"):
                    cyc["asked"] = cyc.get("asked", 0) + 1
                    return _line_check_payload(lib.segments[sid], cyc["asked"], cyc["asked"] + len(cyc["segments"]))
                return _dictation_payload(lib.segments[sid], reason="聞き取れなかった1文を探す")
        # every line was heard: if the questions were still missed, the problem is putting it together
        rec = conn.execute("SELECT context_score, first_listen FROM sentence_studies WHERE id=?", (cyc["record"],)).fetchone()
        if rec and rec["first_listen"] and rec["context_score"] is not None and rec["context_score"] < 1:
            study.update_record(conn, cyc["record"], error_type="meaning")
        return None
    if cyc["stage"] == "study" and cyc.get("target") in lib.segments:
        return study.study_payload(lib.segments[cyc["target"]], cyc.get("tokens") or [], cyc.get("accuracy"))
    if cyc["stage"] == "transfer" and cyc.get("transfer") in study.bank().transfers:
        seg = lib.segments.get(cyc.get("target"))
        return study.transfer_payload(study.bank().transfers[cyc["transfer"]], seg.text if seg else "")
    return None


def _select(conn, s: dict, block: dict) -> dict | None:
    prof = dict(conn.execute("SELECT * FROM profile WHERE id=1").fetchone())
    st = s["state"]
    kind = block["kind"]
    lib = library()

    if kind == "test":
        q = st["test_queue"]
        if not q:
            return None
        item = q.pop(0)
        if item["type"] == "comprehension":
            return _comprehension_payload(lib.clips[item["clip_id"]], False, "test")
        return _dictation_payload(lib.segments[item["segment_id"]])

    if kind in CYCLE_BLOCKS:
        cyc = st.get("cycle")
        if cyc and cyc.get("block") == st["block_index"]:
            item = _cycle_item(conn, s, cyc)
            if item:
                return item
        st["cycle"] = None
        picked = _pick_new_clip(conn, s, kind, prof)
        if not picked:
            return None
        clip, relisten = picked
        if relisten:
            st["notes"].append("未聴の教材を使い切りました。古い音声を再聴しています。「教材」から追加できます")
        st["shown"]["clip"].append(clip.id)
        return _comprehension_payload(clip, relisten, kind)

    if kind in ("review", "final"):
        if kind == "final":
            for cid in st["missed_cards"]:
                if cid in st["final_done"]:
                    continue
                st["final_done"].append(cid)
                ckind, ref = cid.split(":", 1)
                if ckind == "seg" and ref in lib.segments:
                    return _dictation_payload(lib.segments[ref], review_card=cid, reason="今日間違えた文をもう一度")
                if ckind == "vocab" and ref in lib.vocab:
                    p = _vocab_payload(lib.vocab[ref], review_card=cid)
                    if p:
                        return p
                if ckind == "sent" and ref in sentences.bank().items:
                    return sentences.item(sentences.bank().items[ref], reason="今日間違えた文をもう一度")
                if ckind in vocab.KIND_MODALITY:
                    p = vocab.card_item(cid, "今日間違えた単語をもう一度")
                    if p:
                        return p
        p = _due_card_payload(conn, s)
        if p:
            return p
        if kind == "review" and not any(b["kind"] == "words" for b in s["plan"]):
            # short plans have no TOEIC vocabulary block: nothing due -> the review time goes to words
            p = _select(conn, s, {**block, "kind": "words"})
            if p:
                return p
        # warm-up: hear-the-difference drills on easier, already-known material
        st["counter"] += 1
        lo = max(1, prof["level"] - 2)
        for seg in _segment_pool(conn, s, prof, _choose_weak_skill(prof), (lo, prof["level"])):
            d = _discrimination_payload(seg, None)
            if d:
                st["shown"]["segment"].append(seg.id)
                d["public"]["reason"] = "ウォームアップ"
                return d
        return None

    if kind == "weak":
        skill = _choose_weak_skill(prof)
        reason = f"弱点：{SKILLS[skill]}"
        for seg in _segment_pool(conn, s, prof, skill):
            st["shown"]["segment"].append(seg.id)
            if st["counter"] % 2 == 1 and skill in ("weak_forms", "contractions", "endings"):
                d = _discrimination_payload(seg, skill)
                if d:
                    d["public"]["reason"] = reason
                    return d
            return _dictation_payload(seg, reason=reason)
        return None

    if kind == "vocab":
        seen_clips = set(_exposure_counts(conn, "clip")) | set(st["shown"]["clip"])
        shown = set(st["shown"]["vocab"])
        cards = {r["ref"]: dict(r) for r in conn.execute("SELECT * FROM srs_cards WHERE kind='vocab'").fetchall()}
        cands = [v for v in lib.vocab.values()
                 if v["id"] not in shown and v["segment_id"] and lib.clips[v["clip_id"]].pool == "train"
                 and lib.clips[v["clip_id"]].audio and v["level"] <= prof["unlocked"] + 0
                 and v["id"] not in cards
                 and (v["clip_id"] in seen_clips or lib.clips[v["clip_id"]].source not in ("lesson", "user"))]
        cands.sort(key=lambda v: (v["clip_id"] not in seen_clips, abs(v["level"] - prof["level"]), random.random()))
        for v in cands:
            p = _vocab_payload(v)
            if p:
                st["shown"]["vocab"].append(v["id"])
                return p
        return None

    if kind == "d_sound":
        return _diag_sound(conn, s, prof)

    if kind == "d_words":
        did = st.get("vocab_diag")
        if not did:
            did = vocab.start_diagnostic_conn(conn, replace=False, max_items=DIAG_WORDS)["id"]
            st["vocab_diag"] = did
        r = vocab.diag_next_conn(conn, did)
        if r["done"] or not r["item"]:
            return None
        item = dict(r["item"])
        vid = item.pop("item_id")
        item["reason"] = None
        return {"type": "word", "public": {**item, "diagnostic": True}, "secret": {"vocab_item": vid}}

    if kind in ("d_sentence", "sentence"):
        return _sentence_item(conn, s, prof, diagnostic=kind == "d_sentence")

    if kind == "words":
        shown_words = st["shown"].setdefault("word", [])
        p = vocab.pick_next(conn, set(shown_words))
        if not p:
            return None
        shown_words.append(p["secret"]["word_id"])
        if p["secret"].get("card"):
            st["shown"]["card"].append(p["secret"]["card"])
        return p

    if kind == "shadow":
        shadowed = set(st["shown"]["shadow"])
        today_clips = st["shown"]["clip"]
        segs = [seg for cid in reversed(today_clips) for seg in lib.clips[cid].segments if seg.id not in shadowed]
        if not segs:
            segs = _segment_pool(conn, s, prof, None, (max(1, prof["level"] - 1), prof["level"]), exclude_shown="shadow")
        if not segs:
            return None
        seg = segs[0]
        st["shown"]["shadow"].append(seg.id)
        return _shadow_payload(seg)

    raise AssertionError(kind)


def _diag_sound(conn, s: dict, prof: dict) -> dict | None:
    """2-up/1-down over clip levels, rotating the sound category; uses bundled training audio only."""
    st = s["state"]
    ds = st.setdefault("d_sound", {"level": max(1, min(4, prof["level"] if prof["onboarded"] else 2)), "streak": 0})
    lib = library()
    shown = set(st["shown"]["segment"])
    n = st["block_items"][st["block_index"]]
    focus = SOUND_FOCUS[n % len(SOUND_FOCUS)]
    for lv in _level_order(ds["level"]):
        segs = [sg for c in lib.train_clips() if c.source == "builtin" and c.level == lv for sg in c.segments
                if sg.id not in shown and sg.audio]
        random.shuffle(segs)
        for prefer in (focus, None):
            for seg in segs:
                d = _discrimination_payload(seg, prefer)
                if d:
                    st["shown"]["segment"].append(seg.id)
                    d["public"]["reason"] = None
                    return d
    return None


def _sentence_item(conn, s: dict, prof: dict, diagnostic: bool) -> dict | None:
    st = s["state"]
    bank = sentences.bank()
    done = set(st["shown"].setdefault("sentence", []))
    seen = _exposure_counts(conn, "sentence")
    if diagnostic:
        # spread over levels around the learner, two per level, easiest first
        n = st["block_items"][st["block_index"]]
        base = prof["level"] if prof["onboarded"] else 2
        plan = sorted({max(1, min(5, base + d)) for d in (-2, -1, 0, 1, 2)})
        lv = plan[min(len(plan) - 1, n * len(plan) // DIAG_ITEMS["d_sentence"])]
        levels = [lv] + _level_order(lv)
    else:
        levels = _level_order(prof["level"])
    for lv in levels:
        pool = [x for x in bank.by_level(lv) if x.id not in done and bank.audio(x)]
        if not diagnostic:
            pool.sort(key=lambda x: (x.id in seen, (seen.get(x.id) or {}).get("best") or 0, random.random()))
        else:
            random.shuffle(pool)
        if pool:
            x = pool[0]
            st["shown"]["sentence"].append(x.id)
            return sentences.item(x, reason=None if diagnostic else f"構文：{sentences.FOCUS_LABEL[x.focus]}")
    return None


def _unseen_queue(conn, level: int) -> list[dict]:
    """Five never-heard training clips around the learner's level, for re-taking the profile diagnostic."""
    seen = _exposure_counts(conn, "clip")
    lib = library()
    out: list[str] = []
    # five clips spread around the level; at the ends of the scale the nearest levels fill in
    for lv in sorted((max(1, min(5, level + d)) for d in (-2, -1, 0, 1, 2)), key=lambda x: (abs(x - level), x)):
        fresh = [c for c in lib.train_clips() if c.level == lv and c.source == "builtin" and c.id not in seen
                 and c.questions and c.id not in out]
        if not fresh:
            fresh = [c for c in lib.train_clips() if c.source == "builtin" and c.id not in seen and c.questions
                     and c.id not in out and abs(c.level - level) <= 2]
        if fresh:
            out.append(random.choice(fresh).id)
    out.sort(key=lambda cid: lib.clips[cid].level)
    return [{"type": "comprehension", "clip_id": cid} for cid in out]


def _on_complete(conn, s: dict) -> None:
    if s["mode"] == "test":
        _record_test(conn, s)
        abilities.save_snapshot(conn, s["id"], f"checkpoint-{s.get('test_form')}")
    elif s["mode"] == "profile":
        if s.get("test_form"):          # first diagnostic: form A also sets the starting level
            _record_test(conn, s)
        abilities.save_snapshot(conn, s["id"], "diagnostic")
        conn.execute("UPDATE profile SET onboarded=1 WHERE id=1")


def _test_queue(form: str) -> list[dict]:
    out = []
    for clip in library().test_clips(form):
        out.append({"type": "comprehension", "clip_id": clip.id})
        segs = sorted(clip.segments, key=lambda s: len(s.text))
        for seg in sorted(segs[:2], key=lambda s: s.index):
            out.append({"type": "dictation", "segment_id": seg.id})
    return out


# --- answers -----------------------------------------------------------------

def _expose(conn, kind: str, item_id: str, score: float | None) -> None:
    conn.execute(
        """INSERT INTO exposures (kind, item_id, first_seen, last_seen, count, best) VALUES (?,?,?,?,1,?)
           ON CONFLICT(kind, item_id) DO UPDATE SET last_seen=excluded.last_seen, count=count+1,
           best=MAX(COALESCE(best, 0), COALESCE(excluded.best, 0))""",
        (kind, item_id, db.now_iso(), db.now_iso(), score))


def _attempt(conn, s: dict, kind: str, block: str, item_id: str, level: int, score: float, detail: dict) -> None:
    conn.execute("INSERT INTO attempts (session_id, created_at, date, kind, block, item_id, level, score, detail)"
                 " VALUES (?,?,?,?,?,?,?,?,?)",
                 (s["id"], db.now_iso(), db.local_date(), kind, block, item_id, level, score,
                  json.dumps(detail, ensure_ascii=False)))


def answer(session_id: str, item_id: str, body: dict, elapsed: float | None = None) -> dict:
    lib = library()
    with db.tx() as conn:
        s = _load(session_id, conn)
        row = conn.execute("SELECT * FROM pending_items WHERE id=? AND session_id=?", (item_id, session_id)).fetchone()
        if not row:
            raise SessionError(404, "問題が見つかりません")
        if row["answered"]:
            raise SessionError(409, "この問題は回答済みです（別の端末で回答された可能性があります）")
        _account_time(conn, s, elapsed)
        payload = json.loads(row["payload"])
        block = payload["public"]["block"]
        is_test = s["mode"] in ("test", "profile")   # measurement: no level changes mid-way
        typ = payload["type"]
        sec = payload["secret"]
        fb: dict = {"type": typ}
        events = []

        if typ == "comprehension":
            clip = lib.clips[sec["clip_id"]]
            given = list(body.get("answers") or [])
            results = []
            for i, q in enumerate(clip.questions):
                g = given[i] if i < len(given) else None
                right = sec["answers"][i] if i < len(sec.get("answers", [])) else q["answer"]   # index in the shuffled order
                results.append({"correct": g == right, "given": g, "answer": right, "explain": q.get("explain", ""),
                                "type": q.get("type")})
            # a clip without questions (e.g. imported audio with no TOEIC word to ask about) is listening only:
            # it is not scored, so it can neither raise nor lower the level
            score = sum(r["correct"] for r in results) / len(results) if results else None
            fb.update({"score": score, "results": results, "transcript": clip.public(with_text=True)})
            first_listen = not payload["public"].get("relisten") and not conn.execute(
                "SELECT 1 FROM exposures WHERE kind='clip' AND item_id=?", (clip.id,)).fetchone()
            plays = body.get("plays")
            if score is not None:
                _attempt(conn, s, "comprehension", block, clip.id, clip.level, score,
                         {"results": results, "relisten": not first_listen,
                          "plays": int(plays) if isinstance(plays, (int, float)) else None})
            if not is_test or (s["mode"] == "profile" and clip.pool == "train"):
                # training clips heard in a diagnostic are no longer "never heard"
                _expose(conn, "clip", clip.id, score if score is not None else 1.0)
            if not is_test:
                if score is not None:
                    progress.update_skills(conn, {"comprehension": (sum(r["correct"] for r in results), len(results))})
                    ev = progress.update_level(conn, clip.level, comprehension=score)
                    if ev:
                        events.append(ev)
                # Context -> Sentence -> Transfer: dictate lines of this clip to find the one that was not heard
                if block in CYCLE_BLOCKS:
                    tap = block == "commute"
                    s["state"]["cycle"] = {
                        "clip_id": clip.id, "block": s["state"]["block_index"], "stage": "lines",
                        "segments": _cycle_lines(clip, s["state"]["shown"]["segment"]),
                        "streak": 0, "target": None, "tap": tap,
                        "record": study.start_record(conn, s["id"], clip.id, score, first_listen,
                                                     mode="commute" if tap else "standard")}

        elif typ == "dictation":
            seg = lib.segments[sec["segment_id"]]
            res = score_dictation(seg.text, str(body.get("text", ""))[:1000])
            fb.update({"accuracy": res.accuracy, "tokens": res.tokens, "extra": res.extra, "errors": res.errors,
                       "reference": seg.text, "speaker": seg.speaker,
                       "error_labels": {k: SKILLS[k] for k in res.errors}})
            detail = {"accuracy": res.accuracy, "errors": res.errors, "typed": body.get("text", ""),
                      "replays": body.get("replays", 0), "slow": bool(body.get("slow"))}
            _attempt(conn, s, "review" if sec.get("card") else "dictation", block, seg.id, seg.level, res.accuracy, detail)
            if not is_test or s.get("test_form") == "A":
                progress.update_skills(conn, res.category_stats)
            if not is_test:
                _expose(conn, "segment", seg.id, res.accuracy)
                ev = progress.update_level(conn, seg.level, dictation=res.accuracy)
                if ev:
                    events.append(ev)
                rating = srs.rating_from_accuracy(res.accuracy)
                cid = srs.card_id("seg", seg.id)
                # cards are created only for lines the learner actually missed
                r = srs.review(conn, "seg", seg.id, rating, create=res.accuracy < 0.999)
                if r:
                    fb["srs"] = r
                if rating == Rating.Again and cid not in s["state"]["missed_cards"]:
                    s["state"]["missed_cards"].append(cid)
                clip = lib.clips[seg.clip_id]
                if clip.targets:
                    _lesson_context_evidence(conn, clip, seg, res)
                cyc = s["state"].get("cycle")
                if cyc and cyc.get("clip_id") == seg.clip_id and cyc["stage"] == "lines":
                    if res.accuracy < study.STUDY_BELOW:
                        # this is the sentence to work on: explanation next, then a new sentence using it
                        cyc.update(stage="study", target=seg.id, segments=[], tokens=res.tokens, accuracy=res.accuracy)
                        study.update_record(conn, cyc["record"], segment_id=seg.id, dictation_accuracy=res.accuracy,
                                            replays=int(body.get("replays") or 0))
                        fb["study_next"] = True
                    else:
                        cyc["streak"] += 1
                        if cyc["streak"] >= MOVE_ON_STREAK and cyc["segments"]:
                            cyc["segments"] = []
                            fb["moving_on"] = True

        elif typ == "line_check":
            # a self-report, kept apart from dictation accuracy: it moves neither skills nor the level.
            # "sound_only" (heard the words, not the meaning) is studied like a line not heard.
            seg = lib.segments[sec["segment_id"]]
            report = body.get("report")
            if report not in LINE_REPORTS:
                report = "understood" if body.get("heard") is True else "not_heard"
            ok = report == "understood"
            plays = body.get("plays")
            fb.update({"heard": ok, "report": report, "reference": seg.text, "speaker": seg.speaker})
            _attempt(conn, s, "line_check", block, seg.id, seg.level, float(ok),
                     {"report": report, "plays": int(plays) if isinstance(plays, (int, float)) else None})
            if not is_test:
                _expose(conn, "segment", seg.id, float(ok))
                cyc = s["state"].get("cycle")
                if not ok:
                    # the line is reviewed later (by dictation in a normal session)
                    srs.review(conn, "seg", seg.id, Rating.Again, create=True)
                    cid = srs.card_id("seg", seg.id)
                    if cid not in s["state"]["missed_cards"]:
                        s["state"]["missed_cards"].append(cid)
                if cyc and cyc.get("clip_id") == seg.clip_id and cyc["stage"] == "lines":
                    if not ok:
                        cyc.update(stage="study", target=seg.id, segments=[], tokens=[], accuracy=None)
                        study.update_record(conn, cyc["record"], segment_id=seg.id, line_report=report,
                                            replays=int(plays) if isinstance(plays, (int, float)) else None)
                        fb["study_next"] = True
                    else:
                        cyc["streak"] += 1
                        if cyc["streak"] >= MOVE_ON_STREAK and cyc["segments"]:
                            cyc["segments"] = []
                            fb["moving_on"] = True

        elif typ == "discrimination":
            seg = lib.segments[sec["segment_id"]]
            correct = body.get("choice") == sec["answer"]
            fb.update({"correct": correct, "answer": sec["answer"], "target": sec["target"],
                       "distractor": sec["distractor"], "category": SKILLS.get(sec["category"]), "reference": seg.text})
            _attempt(conn, s, "discrimination", block, seg.id, seg.level, float(correct), {"category": sec["category"]})
            if not is_test:
                progress.update_skills(conn, {sec["category"]: (int(correct), 1)})
                _expose(conn, "segment", seg.id, float(correct))

        elif typ == "vocab":
            v = lib.vocab[sec["vocab_id"]]
            typed = " ".join(t.text for t in tokenize(str(body.get("text", ""))))
            want = " ".join(t.text for t in tokenize(v["word"]))
            correct = typed == want or is_typo(want, typed)
            seg = lib.segments[v["segment_id"]]
            fb.update({"correct": correct, "word": v["word"], "ja": v["ja"], "reference": seg.text, "typed": body.get("text", "")})
            _attempt(conn, s, "vocab", block, v["id"], v["level"], float(correct), {"typed": body.get("text", "")})
            if not is_test:
                progress.update_skills(conn, {"vocabulary": (int(correct), 1)})
                _expose(conn, "vocab", v["id"], float(correct))
                rating = Rating.Good if correct else Rating.Again
                r = srs.review(conn, "vocab", v["id"], rating, create=not correct or bool(sec.get("card")))
                if r:
                    fb["srs"] = r
                cid = srs.card_id("vocab", v["id"])
                if not correct and cid not in s["state"]["missed_cards"]:
                    s["state"]["missed_cards"].append(cid)

        elif typ == "word" and sec.get("vocab_item"):
            # profile diagnostic: graded by the vocabulary diagnostic (staircase, follow-ups, no reveal)
            fb = {**vocab.answer_item_conn(conn, sec["vocab_item"], body, session_id=s["id"], block=block), "type": "word"}
            if fb.get("done"):
                _advance(conn, s)

        elif typ == "sentence":
            x = sentences.bank().items[sec["sentence_id"]]
            correct = body.get("choice") == sec["answer"]
            fb.update({"correct": correct, "answer": sec["answer"], "text": x.text, "explain": x.explain,
                       "focus": sentences.FOCUS_LABEL[x.focus], "audio": sentences.bank().audio(x)})
            _attempt(conn, s, "sentence", block, x.id, x.level, float(correct),
                     {"focus": x.focus, "choice": body.get("choice"), "ms": body.get("ms")})
            before = conn.execute("SELECT best FROM exposures WHERE kind='sentence' AND item_id=?", (x.id,)).fetchone()
            if correct and before is not None and not before["best"]:
                from . import growth
                growth.add(conn, "sentence", x.id, f"構文：{sentences.FOCUS_LABEL[x.focus]}",
                           "以前間違えた文の構造を、聞いて正しく理解できました")
            _expose(conn, "sentence", x.id, float(correct))
            if not is_test:
                r = srs.review(conn, "sent", x.id, Rating.Good if correct else Rating.Again, create=not correct)
                if r:
                    fb["srs"] = r
                if not correct and f"sent:{x.id}" not in s["state"]["missed_cards"]:
                    s["state"]["missed_cards"].append(f"sent:{x.id}")

        elif typ == "word":
            fb = {**vocab.grade(conn, payload, body, origin="session", session_id=s["id"], block=block,
                                learn=not is_test), "type": "word"}
            if fb["score"] < 0.5:
                cid = srs.card_id(vocab.CARD_KIND[fb["modality"]], payload["secret"]["word_id"])
                if cid not in s["state"]["missed_cards"]:
                    s["state"]["missed_cards"].append(cid)

        elif typ == "study":
            seg = lib.segments[sec["segment_id"]]
            check = body.get("text_check") if body.get("text_check") in study.TEXT_CHECKS else None
            understood = body.get("meaning_understood")
            understood = bool(understood) if isinstance(understood, bool) else None
            unknown = [str(w)[:40] for w in (body.get("unknown_words") or [])][:12]
            etype = study.classify(check, understood)
            cyc = s["state"].get("cycle") or {}
            ms = body.get("ms")
            if cyc.get("record"):
                study.update_record(conn, cyc["record"], text_check=check, meaning_understood=understood,
                                    unknown_words=json.dumps(unknown, ensure_ascii=False), error_type=etype,
                                    response_ms=int(ms) if isinstance(ms, (int, float)) else None)
            fb.update({"error_type": etype, "error_label": study.ERROR_LABEL[etype], "next_step": study.NEXT_STEP[etype]})
            t = study.transfer_for(conn, seg) if not is_test else None
            if cyc and t:
                cyc.update(stage="transfer", transfer=t.id)
                fb["transfer_next"] = True
            elif cyc:
                cyc["stage"] = "done"

        elif typ == "transfer":
            t = study.bank().transfers[sec["transfer_id"]]
            correct = body.get("choice") == sec["answer"]
            ms = body.get("ms")
            fb.update({"correct": correct, "answer": sec["answer"], "text": t.text, "form": t.form, "ja": t.ja,
                       "practice": sec.get("practice"), "audio": study.bank().audio(t)})
            # a never-heard sentence: first-listen evidence of its own, kept apart from the practised line
            _attempt(conn, s, "transfer", block, t.id, t.level, float(correct),
                     {"choice": body.get("choice"), "plays": body.get("plays"), "ms": ms})
            _expose(conn, "transfer", t.id, float(correct))
            cyc = s["state"].get("cycle") or {}
            if cyc.get("record"):
                study.update_record(conn, cyc["record"], transfer_id=t.id, transfer_correct=int(correct),
                                    transfer_ms=int(ms) if isinstance(ms, (int, float)) else None)
            if cyc:
                cyc["stage"] = "done"
            if correct:
                from . import growth
                growth.add(conn, "transfer", t.id, f"応用：{t.form}",
                           "練習した表現を、初めて聞く別の文でも聞き取れました")
            elif not is_test and cyc.get("target"):
                # not transferred yet: the practised line comes back sooner
                srs.review(conn, "seg", cyc["target"], Rating.Again, create=True)

        elif typ == "shadowing":
            seg = lib.segments[sec["segment_id"]]
            rating_val = int(body.get("rating", 2))  # 1 難しい / 2 まあまあ / 3 できた
            fb.update({"rating": rating_val, "reference": seg.text})
            _attempt(conn, s, "shadowing", block, seg.id, seg.level, rating_val / 3, {"rating": rating_val})
            if rating_val == 1:
                srs.review(conn, "seg", seg.id, Rating.Hard, create=True)
        else:
            raise SessionError(400, "不明な問題タイプ")

        conn.execute("UPDATE pending_items SET answered=1 WHERE id=?", (item_id,))
        if events:
            s["state"]["events"].extend(events)
            fb["events"] = events
        _save(conn, s)
    fb["session"] = public_session(s)
    return fb


def _lesson_context_evidence(conn, clip: Clip, seg: Segment, res) -> None:
    """In a generated lesson, a target word heard correctly inside the sentence is context evidence."""
    from .vocab_data import vocab_library
    words = vocab_library().words
    for wid in clip.targets:
        w = words.get(wid)
        if not w or not w.appears_in(seg.text):
            continue
        forms = {f.lower() for f in w.forms()}
        hits = [t for t in res.tokens if t["text"] in forms]
        if hits:
            vocab.record_context(conn, wid, all(t["status"] in ("correct", "typo") for t in hits))


# --- results -------------------------------------------------------------------

def summary(session_id: str) -> dict:
    s = _load(session_id)
    rows = [dict(r) for r in db.q("SELECT * FROM attempts WHERE session_id=? ORDER BY id", (session_id,))]
    by_kind: dict[str, list[float]] = {}
    errors: dict[str, int] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r["score"])
        if r["kind"] in ("dictation", "review"):
            for k, n in json.loads(r["detail"]).get("errors", {}).items():
                errors[k] = errors.get(k, 0) + n
    avg = {k: round(sum(v) / len(v), 3) for k, v in by_kind.items() if v}
    test = db.q1("SELECT * FROM tests WHERE session_id=?", (session_id,))
    return {
        "session": public_session(s),
        "items": len(rows),
        "by_kind": {k: {"count": len(v), "average": avg[k]} for k, v in by_kind.items()},
        "errors": [{"skill": k, "label": SKILLS[k], "count": n} for k, n in sorted(errors.items(), key=lambda x: -x[1])],
        "new_clips": len([r for r in rows if r["kind"] == "comprehension"]),
        "events": s["state"].get("events", []),
        "test": _test_public(test) if test else None,
        "profile": _profile_summary(session_id) if s["mode"] in ("profile", "test") else None,
    }


def _profile_summary(session_id: str) -> dict | None:
    row = db.q1("SELECT * FROM ability_snapshots WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,))
    if not row:
        return None
    cur = abilities.current()
    prev = db.q1("SELECT data FROM ability_snapshots WHERE id<? ORDER BY id DESC LIMIT 1", (row["id"],))
    return {"measured": json.loads(row["data"]), "previous": json.loads(prev["data"]) if prev else None,
            "labels": abilities.LABEL, "details": abilities.DETAIL, "targets": abilities.TARGET,
            "priority": abilities.priority(cur), "advice": abilities.advice(cur), "source": row["source"]}


def _test_public(t) -> dict:
    d = dict(t)
    d["detail"] = json.loads(d["detail"] or "{}")
    return d


def _record_test(conn, s: dict) -> None:
    if conn.execute("SELECT 1 FROM tests WHERE session_id=?", (s["id"],)).fetchone():
        return
    rows = conn.execute("SELECT * FROM attempts WHERE session_id=? AND kind IN ('dictation','review','comprehension')",
                        (s["id"],)).fetchall()
    per_level: dict[int, dict[str, list[float]]] = {}
    for r in rows:
        k = "dictation" if r["kind"] in ("dictation", "review") else "comprehension"
        per_level.setdefault(r["level"], {"dictation": [], "comprehension": []})[k].append(r["score"])
    if not rows:
        return
    dict_all = [r["score"] for r in rows if r["kind"] in ("dictation", "review")]
    comp_all = [r["score"] for r in rows if r["kind"] == "comprehension"]
    d = sum(dict_all) / len(dict_all) if dict_all else 0
    c = sum(comp_all) / len(comp_all) if comp_all else 0
    estimate = 1
    for lv in range(1, 6):
        st = per_level.get(lv)
        if not st:
            break
        dl = sum(st["dictation"]) / len(st["dictation"]) if st["dictation"] else 0
        cl = sum(st["comprehension"]) / len(st["comprehension"]) if st["comprehension"] else 0
        if dl >= 0.7 and cl >= 0.5:
            estimate = lv
        else:
            break
    level_detail = {lv: {k: round(sum(v) / len(v), 3) if v else None for k, v in st.items()} for lv, st in per_level.items()}
    score = round(100 * (0.6 * d + 0.4 * c), 1)
    conn.execute("INSERT INTO tests (form, session_id, taken_at, day, dictation, comprehension, score, level_estimate, detail)"
                 " VALUES (?,?,?,?,?,?,?,?,?)",
                 (s["test_form"], s["id"], db.now_iso(), progress.day_number(), round(d, 3), round(c, 3), score, estimate,
                  json.dumps({"levels": level_detail})))
    # the first checkpoint taken (whichever form the learner drew) sets the starting level
    prof = conn.execute("SELECT onboarded FROM profile WHERE id=1").fetchone()
    if not prof["onboarded"]:
        conn.execute("UPDATE profile SET onboarded=1, level=?, unlocked=? WHERE id=1", (estimate, estimate))
