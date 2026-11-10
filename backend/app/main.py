from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import abilities, analytics, asr, config, db, engine, growth, imports, lessons, sentences, planner, progress, srs, tts, vocab
from .content import library, reload_library
from .vocab_data import vocab_library

app = FastAPI(title="ListenFlow API", version="1.0.0")


@app.exception_handler(engine.SessionError)
def _session_error(_: Request, exc: engine.SessionError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.msg})


@app.exception_handler(vocab.VocabError)
def _vocab_error(_: Request, exc: vocab.VocabError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.msg})


@app.exception_handler(lessons.LessonError)
def _lesson_error(_: Request, exc: lessons.LessonError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.msg, "checks": exc.checks})


@app.exception_handler(imports.ImportFailed)
def _import_error(_: Request, exc: imports.ImportFailed):
    return JSONResponse(status_code=exc.status, content={"detail": exc.msg})


# --- profile / settings ---------------------------------------------------------

@app.get("/health")
def health():
    lib = library()
    vl = vocab_library()
    return {"ok": True, "clips": len(lib.clips), "missing_audio": lib.missing_audio(),
            "words": len(vl.words), "missing_word_audio": vl.missing_audio(), "asr": asr.available()[0],
            "sentences": len(sentences.bank().items), "missing_sentence_audio": sentences.bank().missing_audio()}


@app.get("/profile")
def get_profile():
    prof = db.profile()
    day = progress.day_number()
    active = engine.active_session()
    return {
        "onboarded": bool(prof["onboarded"]), "level": prof["level"], "unlocked": prof["unlocked"],
        "level_name": progress.LEVEL_NAMES[prof["level"]], "settings": prof["settings"],
        "day": day, "curriculum_days": config.CURRICULUM_DAYS, "phase": progress.phase(day),
        "today_minutes": round(progress.today_seconds() / 60, 1), "streak": progress.streak(),
        "review_due": srs.due_count(), "tests": progress.tests_status(), "active_session": active,
        "vocab": {"diagnosed": bool(db.q1("SELECT 1 FROM vocab_diagnostics WHERE status='completed'")),
                  "active_diagnostic": bool(db.q1("SELECT 1 FROM vocab_diagnostics WHERE status='active'")),
                  "due": vocab.due_count()},
        "listening_profile": _listening_profile_brief(),
    }


def _listening_profile_brief() -> dict:
    cur = abilities.current()
    return {"diagnosed": bool(db.q1("SELECT 1 FROM ability_snapshots WHERE source='diagnostic'")),
            "abilities": cur, "priority": abilities.priority(cur), "advice": abilities.advice(cur)}


class SettingsIn(BaseModel):
    daily_goal: int | None = Field(None, ge=5, le=300)
    break_enabled: bool | None = None
    break_minutes: int | None = Field(None, ge=3, le=10)
    break_every: int | None = Field(None, ge=20, le=120)
    playback_rate: float | None = Field(None, ge=0.5, le=1.5)
    auto_lessons: bool | None = None
    lesson_engine: Literal["auto", "gemini", "local"] | None = None
    lesson_voice: Literal["mac", "gemini"] | None = None
    gemini_model: str | None = Field(None, pattern=r"^gemini-[a-z0-9][a-z0-9.\-]{1,60}$")
    interests: list[Literal["tech", "business", "conversation", "science", "travel", "daily"]] | None = Field(None, max_length=6)


@app.put("/settings")
def put_settings(body: SettingsIn):
    with db.tx() as conn:
        cur = json.loads(conn.execute("SELECT settings FROM profile WHERE id=1").fetchone()["settings"])
        cur.update({k: v for k, v in body.model_dump().items() if v is not None})
        conn.execute("UPDATE profile SET settings=? WHERE id=1", (json.dumps(cur),))
    return db.profile()["settings"]


class ManualLevel(BaseModel):
    level: int = Field(ge=1, le=5)


@app.post("/onboarding/skip")
def skip_onboarding(body: ManualLevel):
    with db.tx() as conn:
        conn.execute("UPDATE profile SET onboarded=1, level=?, unlocked=MAX(unlocked, ?) WHERE id=1", (body.level, body.level))
    return get_profile()


# --- sessions -------------------------------------------------------------------

class PlanIn(BaseModel):
    minutes: int = Field(ge=5, le=300)
    mode: str = "standard"


@app.post("/plan/preview")
def plan_preview(body: PlanIn):
    return planner.summarize(planner.build_plan(body.minutes, db.profile()["settings"], body.mode, abilities.current()))


class SessionIn(BaseModel):
    minutes: int = Field(30, ge=0, le=300)
    mode: str = Field("standard", pattern="^(standard|review|test|profile)$")
    replace: bool = False
    form: str | None = None
    focus_clip: str | None = Field(None, max_length=80)


@app.post("/sessions")
def create_session(body: SessionIn):
    return engine.create_session(body.minutes, body.mode, body.replace, body.form, body.focus_clip)


# --- listening profile / recommendations ----------------------------------------------

@app.get("/v1/listening-profile")
def listening_profile():
    return {**_listening_profile_brief(), "history": abilities.history(), "kpis": analytics.kpis(),
            "growth": growth.recent(14, 20)}


@app.get("/v1/recommendations")
def recommendations(limit: int = 6):
    from .content import TOPIC_LABEL
    prof = db.profile()
    interests = prof["settings"].get("interests") or []
    seen = {r["item_id"] for r in db.q("SELECT item_id FROM exposures WHERE kind='clip'")}
    lv = prof["level"]
    cands = [c for c in library().train_clips() if c.id not in seen and c.audio and abs(c.level - lv) <= 1]
    cands.sort(key=lambda c: (c.topic not in interests, interests.index(c.topic) if c.topic in interests else 9,
                              abs(c.level - lv), c.id))
    return {"items": [{"id": c.id, "title": c.title, "topic": c.topic, "topic_label": TOPIC_LABEL.get(c.topic, c.topic),
                       "level": c.level, "kind": c.kind, "duration": c.duration, "speakers": c.speakers,
                       "source": c.source} for c in cands[:max(1, min(limit, 20))]],
            "interests": interests, "topics": TOPIC_LABEL}


@app.get("/v1/checkpoints")
def checkpoint_overview():
    from . import checkpoints
    return checkpoints.overview()


@app.get("/v1/growth")
def growth_events(days: int = 7):
    return {"items": growth.recent(max(1, min(days, 60)), 20)}


@app.get("/sessions/active")
def active_session():
    return {"session": engine.active_session()}


@app.get("/sessions/{sid}")
def get_session(sid: str):
    return engine.public_session(engine._load(sid))


class Elapsed(BaseModel):
    elapsed: float | None = Field(None, ge=0)
    status: str | None = None


@app.post("/sessions/{sid}/next")
def next_item(sid: str, body: Elapsed):
    return engine.next_item(sid, body.elapsed)


@app.post("/sessions/{sid}/heartbeat")
def heartbeat(sid: str, body: Elapsed):
    return engine.heartbeat(sid, body.elapsed, body.status)


class AnswerIn(BaseModel):
    item_id: str
    elapsed: float | None = Field(None, ge=0)
    text: str | None = None
    answers: list[int | None] | None = None
    choice: int | None = None
    rating: int | None = Field(None, ge=1, le=3)
    confidence: Literal["sure", "guess", "unknown"] | None = None
    ms: int | None = Field(None, ge=0)
    replays: int = 0
    plays: int | None = Field(None, ge=0, le=100)   # comprehension: how many times the audio was started
    slow: bool = False


@app.post("/sessions/{sid}/answer")
def answer(sid: str, body: AnswerIn):
    return engine.answer(sid, body.item_id, body.model_dump(), body.elapsed)


class ExtendIn(BaseModel):
    minutes: int = Field(15, ge=5, le=120)


@app.post("/sessions/{sid}/extend")
def extend(sid: str, body: ExtendIn):
    return engine.extend(sid, body.minutes)


@app.post("/sessions/{sid}/skip-block")
def skip_block(sid: str):
    return engine.skip_block(sid)


@app.post("/sessions/{sid}/finish")
def finish(sid: str):
    out = engine.finish(sid)
    maybe_auto_lesson()
    return out


@app.get("/sessions/{sid}/summary")
def summary(sid: str):
    return engine.summary(sid)


@app.post("/sessions/{sid}/abandon")
def abandon(sid: str):
    with db.tx() as conn:
        conn.execute("UPDATE sessions SET status='abandoned', updated_at=? WHERE id=? AND status IN ('active','paused')",
                     (db.now_iso(), sid))
    return {"ok": True}


# --- analytics / library -------------------------------------------------------------

@app.get("/analytics")
def get_analytics():
    return analytics.overview()


@app.get("/library")
def get_library():
    lib = library()
    seen = {r["item_id"]: dict(r) for r in db.q("SELECT * FROM exposures WHERE kind='clip'")}
    prof = db.profile()
    items = []
    for c in lib.clips.values():
        if c.pool != "train":
            continue
        e = seen.get(c.id)
        items.append({"id": c.id, "title": c.title, "level": c.level, "kind": c.kind, "speakers": c.speakers,
                      "lines": len(c.segments), "duration": c.duration, "source": c.source, "has_audio": bool(c.audio),
                      "heard": bool(e), "times": e["count"] if e else 0, "best": e["best"] if e else None,
                      "locked": c.level > prof["unlocked"] + 1})
    items.sort(key=lambda x: (x["level"], x["source"] != "user", x["title"]))
    ok, msg = tts.available()
    return {"items": items, "tts": {"available": ok, "message": msg},
            "voices": ["Samantha", "Daniel", "Karen", "Moira", "Tessa", "Rishi"]}


@app.get("/library/{clip_id}")
def get_clip(clip_id: str):
    c = library().clips.get(clip_id)
    if not c or c.pool != "train":
        raise HTTPException(404, "教材が見つかりません")
    heard = db.q1("SELECT 1 FROM exposures WHERE kind='clip' AND item_id=?", (clip_id,))
    # transcripts of unheard material stay hidden so it remains 初見 when it comes up in training
    return {**c.public(with_text=bool(heard) or c.source == "user"), "heard": bool(heard),
            "questions": len(c.questions), "vocab": c.vocab if heard else []}


class Line(BaseModel):
    speaker: str = Field(pattern="^[A-D]$")
    text: str = Field(min_length=1, max_length=400)


class Question(BaseModel):
    q: str
    choices: list[str] = Field(min_length=2, max_length=4)
    answer: int = Field(ge=0, le=3)
    explain: str = ""
    type: str = "detail"


class NewClip(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    level: int = Field(ge=1, le=5)
    kind: str = "dialogue"
    voices: dict[str, str]
    lines: list[Line] = Field(min_length=1, max_length=40)
    questions: list[Question] = Field(default_factory=list, max_length=6)
    vocab: list[tuple[str, str]] = Field(default_factory=list, max_length=10)


@app.post("/library")
def add_clip(body: NewClip):
    ok, msg = tts.available()
    if not ok:
        raise HTTPException(503, msg)
    allowed = {"Samantha", "Daniel", "Karen", "Moira", "Tessa", "Rishi"}
    if any(v not in allowed for v in body.voices.values()):
        raise HTTPException(400, "対応していない音声です")
    for ln in body.lines:
        if ln.speaker not in body.voices:
            raise HTTPException(400, f"話者 {ln.speaker} の音声が指定されていません")
    for q in body.questions:
        if q.answer >= len(q.choices):
            raise HTTPException(400, "正解番号が選択肢の範囲外です")
    slug = re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-")[:24] or "clip"
    cid = f"u-{slug}-{db.now().strftime('%y%m%d%H%M%S')}"
    raw: dict[str, Any] = {
        "id": cid, "level": body.level, "pool": "train", "kind": body.kind, "title": body.title,
        "voices": body.voices, "lines": [[ln.speaker, ln.text.strip()] for ln in body.lines],
        "questions": [q.model_dump() for q in body.questions],
        "vocab": [[w, ja] for w, ja in body.vocab if re.search(rf"\b{re.escape(w)}\b", " ".join(l.text for l in body.lines), re.I)],
    }
    tts.synthesize_one(raw)
    config.USER_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    (config.USER_CONTENT_DIR / f"{cid}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1))
    reload_library()
    return {"id": cid}


@app.delete("/library/{clip_id}")
def delete_clip(clip_id: str):
    if not imports.remove_user_clip(clip_id):
        raise HTTPException(404, "削除できるのは自分で追加した教材だけです")
    reload_library()
    return {"ok": True}


# --- tests / audio / export -------------------------------------------------------------

@app.get("/tests")
def tests():
    rows = [engine._test_public(r) for r in db.q("SELECT * FROM tests ORDER BY taken_at")]
    return {"status": progress.tests_status(), "history": rows}


@app.get("/audio/{path:path}")
def audio(path: str):
    base = config.AUDIO_DIR.resolve()
    p = (base / path).resolve()
    if base not in p.parents or not p.is_file() or p.suffix != ".mp3":
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/export")
def export():
    out = {}
    for t in ("profile", "skill_stats", "level_stats", "exposures", "sessions", "attempts", "srs_cards", "daily_log", "tests",
              "user_vocab", "vocab_diagnostics", "audio_lessons", "imports", "ability_snapshots", "growth_events",
              "checkpoint_plan"):
        out[t] = [dict(r) for r in db.q(f"SELECT * FROM {t}")]
    return JSONResponse(out, headers={"Content-Disposition": "attachment; filename=listenflow-export.json"})


@app.get("/system")
def system():
    """Addresses other devices can use to open the web app."""
    import os
    import shutil
    import socket
    import subprocess

    port = int(os.environ.get("LISTENFLOW_WEB_PORT", "3210"))
    urls = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the LAN interface
        urls.append({"label": "同じ Wi-Fi", "url": f"http://{s.getsockname()[0]}:{port}"})
        s.close()
    except OSError:
        pass
    host = socket.gethostname()
    if host.endswith(".local"):
        urls.append({"label": "同じ Wi-Fi (名前)", "url": f"http://{host}:{port}"})
    ts = None
    exe = shutil.which("tailscale") or ("/Applications/Tailscale.app/Contents/MacOS/Tailscale"
                                         if os.path.exists("/Applications/Tailscale.app/Contents/MacOS/Tailscale") else None)
    if exe:
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=3).stdout.strip().splitlines()
            if out:
                ts = out[0]
                urls.append({"label": "Tailscale", "url": f"http://{ts}:{port}"})
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"urls": urls, "tailscale": ts, "hostname": host}


# --- vocabulary (v1) ---------------------------------------------------------------------
# ListenFlow is a single-learner app: every route reads and writes the one local learning
# profile stored on this Mac, so there is no other learner's history to reach.

log = logging.getLogger("listenflow")


class DiagnosticIn(BaseModel):
    replace: bool = False


@app.post("/v1/vocabulary/diagnostics")
def create_diagnostic(body: DiagnosticIn):
    return vocab.start_diagnostic(body.replace)


@app.post("/v1/vocabulary/diagnostics/{did}/next")
def diagnostic_next(did: str):
    return vocab.diag_next(did)


@app.get("/v1/vocabulary/diagnostics/{did}")
def diagnostic_get(did: str):
    with db.tx() as conn:
        return vocab.diag_public(vocab._diag_load(conn, did))


class WordAnswerIn(BaseModel):
    item_id: str = Field(min_length=1, max_length=40)
    choice: int | None = Field(None, ge=0, le=3)
    confidence: Literal["sure", "guess", "unknown"] = "sure"
    ms: int | None = Field(None, ge=0)


@app.post("/v1/vocabulary/answers")
def vocabulary_answer(body: WordAnswerIn):
    fb = vocab.answer_item(body.item_id, body.model_dump())
    if fb.get("done") and not fb.get("duplicate"):
        maybe_auto_lesson()
    return fb


@app.get("/v1/vocabulary/reviews")
def vocabulary_reviews(limit: int = 20, include_new: bool = False):
    return vocab.review_items(max(1, min(limit, 50)), include_new)


@app.post("/v1/vocabulary/reviews")
def vocabulary_review_answer(body: WordAnswerIn):
    return vocab.answer_item(body.item_id, body.model_dump())


@app.get("/v1/vocabulary/stats")
def vocabulary_stats():
    out = vocab.stats()
    out["lessons"] = lessons.list_lessons()
    out["generator"] = lessons.engine_status()
    out["auto_lesson_running"] = _auto_lock.locked()
    return out


class LessonIn(BaseModel):
    words: list[str] | None = Field(None, max_length=4)
    engine: Literal["auto", "gemini", "local"] | None = None


@app.post("/v1/audio-lessons/generate")
def generate_lesson(body: LessonIn):
    return lessons.generate(body.words, body.engine)


@app.get("/v1/audio-lessons")
def list_audio_lessons():
    return {"lessons": lessons.list_lessons(), "generator": lessons.engine_status()}


@app.delete("/v1/audio-lessons/{lid}")
def delete_audio_lesson(lid: str):
    if not lessons.delete_lesson(lid):
        raise HTTPException(404, "教材が見つかりません")
    return {"ok": True}


class GeminiKeyIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=200)


# The key is write-only over HTTP: it is stored in DATA_DIR/secrets (0600) and never returned,
# only its last 4 characters for recognition.
@app.get("/v1/integrations/gemini")
def gemini_status():
    return lessons.engine_status()


@app.put("/v1/integrations/gemini")
def gemini_save_key(body: GeminiKeyIn):
    lessons.save_key(body.api_key)
    return lessons.engine_status()


@app.delete("/v1/integrations/gemini")
def gemini_delete_key():
    lessons.delete_key()
    return lessons.engine_status()


@app.post("/v1/integrations/gemini/test")
def gemini_test():
    return {**lessons.test_gemini(), **lessons.engine_status()}


# --- imported audio (NotebookLM Audio Overview etc.) ---------------------------------------

@app.get("/v1/imports")
def list_imports():
    ok, msg = asr.available()
    return {"imports": imports.list_jobs(), "asr": {"available": ok, "message": msg, "model": asr.MODEL}}


@app.post("/v1/imports/notebooklm")
def import_notebooklm(file: UploadFile = File(...), title: str = Form("NotebookLM", max_length=60),
                      level: int | None = Form(None, ge=1, le=5), script: str | None = Form(None, max_length=200_000)):
    import tempfile
    from pathlib import Path
    config.ensure_dirs()
    imports.imports_dir().mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=imports.imports_dir(), suffix=".upload")
    size = 0
    with os.fdopen(fd, "wb") as out:
        while chunk := file.file.read(1 << 20):
            size += len(chunk)
            if size > imports.MAX_BYTES:
                out.close()
                Path(tmp).unlink(missing_ok=True)
                raise HTTPException(413, f"ファイルが大きすぎます（上限 {imports.MAX_BYTES // 1024 // 1024}MB）")
            out.write(chunk)
    title = title.strip() or "NotebookLM"
    return imports.start(Path(tmp), file.filename or "audio", title, level or db.profile()["level"], script)


@app.get("/v1/imports/{jid}")
def get_import(jid: str):
    job = imports.get_job(jid)
    if not job:
        raise HTTPException(404, "取り込みが見つかりません")
    return job


@app.delete("/v1/imports/{jid}")
def delete_import(jid: str):
    if not imports.delete_job(jid):
        raise HTTPException(404, "取り込みが見つかりません")
    return {"ok": True}


_auto_lock = threading.Lock()


def maybe_auto_lesson() -> None:
    """After a diagnostic or a session, prepare one listening lesson from weak words in the
    background (only when none is waiting to be heard). Failures are logged, never raised."""
    if os.environ.get("LISTENFLOW_AUTO_LESSONS", "1") == "0" or not db.profile()["settings"].get("auto_lessons", True):
        return
    if not tts.available()[0] or _auto_lock.locked():
        return
    weak = db.q1("SELECT COUNT(*) n FROM user_vocab WHERE MIN(COALESCE(text_mastery,1), COALESCE(audio_mastery,1),"
                 " COALESCE(context_mastery,1)) < 0.8")["n"]
    if weak < 2 or lessons.unheard_count() > 0:
        return

    def run():
        with _auto_lock:
            try:
                lessons.generate()
            except Exception as e:  # noqa: BLE001
                log.warning("auto lesson generation failed: %s", e)

    threading.Thread(target=run, daemon=True).start()
