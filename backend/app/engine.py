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

from . import db, planner, progress, srs, vocab
from .content import Clip, Segment, library
from .scoring import SKILLS, is_typo, make_discrimination, score_dictation, tokenize

MAX_ELAPSED_REPORT = 120     # seconds accepted per time report (guards against sleeping tabs)
MOVE_ON_ACCURACY = 0.9       # two consecutive segments at this accuracy -> new audio
MOVE_ON_STREAK = 2


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
            "clip_queue": None, "missed_cards": [], "final_done": [], "counter": 0,
            "test_queue": None, "events": [], "notes": []}


def active_session() -> dict | None:
    row = db.q1("SELECT id FROM sessions WHERE status IN ('active','paused') ORDER BY updated_at DESC LIMIT 1")
    return public_session(_load(row["id"])) if row else None


def create_session(minutes: int, mode: str = "standard", replace: bool = False, form: str | None = None) -> dict:
    prof = db.profile()
    if mode == "test":
        if form not in ("A", "B", "C"):
            raise SessionError(400, "不明なテストです")
        plan = [planner._block("test", 0)]
    else:
        if not 5 <= minutes <= 300:
            raise SessionError(400, "学習時間は 5〜300 分で指定してください")
        plan = planner.build_plan(minutes, prof["settings"], mode)
    with db.tx() as conn:
        cur = conn.execute("SELECT id FROM sessions WHERE status IN ('active','paused')").fetchall()
        if cur and not replace:
            raise SessionError(409, "進行中のセッションがあります")
        for r in cur:
            conn.execute("UPDATE sessions SET status='abandoned', updated_at=? WHERE id=?", (db.now_iso(), r["id"]))
        sid = uuid.uuid4().hex[:12]
        state = _new_state(len(plan))
        if mode == "test":
            state["test_queue"] = _test_queue(form)
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
            "planned_study_minutes": planner.study_minutes(s["plan"]), "notes": st.get("notes", [])[-3:]}


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
        extra = planner.extra_blocks(minutes, start=s["state"]["counter"])
        plan = s["plan"]
        idx = s["state"]["block_index"]
        # put extension before an unstarted closing block (shadowing / final review)
        insert_at = len(plan)
        while insert_at - 1 > idx and plan[insert_at - 1]["kind"] in ("shadow", "final"):
            insert_at -= 1
        s["plan"] = plan[:insert_at] + extra + plan[insert_at:]
        bs = s["state"]["block_seconds"]
        s["state"]["block_seconds"] = bs[:insert_at] + [0.0] * len(extra) + bs[insert_at:]
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
            if s["mode"] == "test":
                _record_test(conn, s)
            _save(conn, s)
    return summary(session_id)


def _advance(conn, s: dict) -> None:
    st = s["state"]
    st["block_index"] += 1
    st["clip_queue"] = None
    conn.execute("UPDATE pending_items SET answered=2 WHERE session_id=? AND answered=0", (s["id"],))
    if st["block_index"] >= len(s["plan"]):
        s["status"] = "completed"
        s["completed_at"] = db.now_iso()
        if s["mode"] == "test":
            _record_test(conn, s)


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
            if block["kind"] != "test" and st["block_seconds"][st["block_index"]] >= limit:
                _advance(conn, s)
                continue
            payload = _select(conn, s, block)
            if payload is None:
                note = f"「{block['label']}」に出せる教材がなくなったため次へ進みました"
                if block["kind"] != "test":
                    st["notes"].append(note)
                _advance(conn, s)
                continue
            pid = uuid.uuid4().hex[:12]
            payload["public"]["item_id"] = pid
            payload["public"]["block"] = block["kind"]
            conn.execute("INSERT INTO pending_items (id, session_id, created_at, payload) VALUES (?,?,?,?)",
                         (pid, session_id, db.now_iso(), json.dumps(payload)))
            st["counter"] += 1
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
            # lessons built from the learner's weak words, then user-added material, then random
            fresh.sort(key=lambda c: ({"lesson": 0, "user": 1}.get(c.source, 2), random.random()))
            return fresh[0], False
    # every clip has been heard: re-listen to the one heard longest ago (never twice in one session)
    old = [c for c in clips if c.id not in shown and abs(c.level - prof["level"]) <= 1]
    if not old:
        return None
    old.sort(key=lambda c: seen.get(c.id, {}).get("last_seen") or "")
    return old[0], True


def _comprehension_payload(clip: Clip, relisten: bool, block: str) -> dict:
    return {
        "type": "comprehension",
        "public": {"type": "comprehension", "clip": clip.public(with_text=False), "title": clip.title,
                   "level": clip.level, "relisten": relisten,
                   "questions": [{"q": q["q"], "choices": q["choices"], "type": q.get("type")} for q in clip.questions]},
        "secret": {"clip_id": clip.id, "answers": [q["answer"] for q in clip.questions]},
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

    if kind in ("new", "multi", "long"):
        cq = st.get("clip_queue")
        if cq and cq.get("block") == st["block_index"] and cq["segments"]:
            seg = lib.segments[cq["segments"].pop(0)]
            st["shown"]["segment"].append(seg.id)
            return _dictation_payload(seg, reason="この音声を細部まで聞き取る")
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
                if ckind in vocab.KIND_MODALITY:
                    p = vocab.card_item(cid, "今日間違えた単語をもう一度")
                    if p:
                        return p
        p = _due_card_payload(conn, s)
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
        is_test = s["mode"] == "test"
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
                results.append({"correct": g == q["answer"], "given": g, "answer": q["answer"], "explain": q.get("explain", ""),
                                "type": q.get("type")})
            # a clip without questions (e.g. imported audio with no TOEIC word to ask about) is listening only:
            # it is not scored, so it can neither raise nor lower the level
            score = sum(r["correct"] for r in results) / len(results) if results else None
            fb.update({"score": score, "results": results, "transcript": clip.public(with_text=True)})
            if score is not None:
                _attempt(conn, s, "comprehension", block, clip.id, clip.level, score, {"results": results})
            if not is_test:
                _expose(conn, "clip", clip.id, score if score is not None else 1.0)
                if score is not None:
                    progress.update_skills(conn, {"comprehension": (sum(r["correct"] for r in results), len(results))})
                    ev = progress.update_level(conn, clip.level, comprehension=score)
                    if ev:
                        events.append(ev)
                # adaptive follow-up: dictate lines of this clip until the learner is solid, then move on
                if block in ("new", "multi", "long"):
                    s["state"]["clip_queue"] = {"clip_id": clip.id, "block": s["state"]["block_index"],
                                                "segments": [sg.id for sg in clip.segments if sg.id not in s["state"]["shown"]["segment"]],
                                                "streak": 0}

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
                cq = s["state"].get("clip_queue")
                if cq and cq.get("clip_id") == seg.clip_id:
                    cq["streak"] = cq["streak"] + 1 if res.accuracy >= MOVE_ON_ACCURACY else 0
                    if cq["streak"] >= MOVE_ON_STREAK and cq["segments"]:
                        cq["segments"] = []
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

        elif typ == "word":
            fb = {**vocab.grade(conn, payload, body, origin="session", session_id=s["id"], block=block,
                                learn=not is_test), "type": "word"}
            if fb["score"] < 0.5:
                cid = srs.card_id(vocab.CARD_KIND[fb["modality"]], payload["secret"]["word_id"])
                if cid not in s["state"]["missed_cards"]:
                    s["state"]["missed_cards"].append(cid)

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
    }


def _test_public(t) -> dict:
    d = dict(t)
    d["detail"] = json.loads(d["detail"] or "{}")
    return d


def _record_test(conn, s: dict) -> None:
    if conn.execute("SELECT 1 FROM tests WHERE session_id=?", (s["id"],)).fetchone():
        return
    rows = conn.execute("SELECT * FROM attempts WHERE session_id=?", (s["id"],)).fetchall()
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
    if s["test_form"] == "A":
        prof = conn.execute("SELECT onboarded FROM profile WHERE id=1").fetchone()
        if not prof["onboarded"]:
            conn.execute("UPDATE profile SET onboarded=1, level=?, unlocked=? WHERE id=1", (estimate, estimate))
