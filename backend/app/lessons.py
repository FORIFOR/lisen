"""Audio lessons built around the learner's weak words.

Pipeline: pick target words -> write a short dialogue (Gemini when an API key is configured,
otherwise - or when Gemini fails in "auto" mode - a local template generator) -> quality checks on the text -> synthesize audio
locally (macOS `say`, so the transcript is the ground truth) -> quality checks on the audio
-> store as a listening clip. Rejected lessons are deleted, never served.

Only the target words and a level are sent to Gemini; no learning history leaves the Mac.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import urllib.error
import urllib.request

from . import asr, config, db, tts, vocab
from .content import reload_library
from .vocab_data import Word, vocab_library

GENERATOR_VERSION = 2   # 2: speech-recognition checks
MIN_SECONDS, MAX_SECONDS = 8.0, 150.0
WORDS_PER_SECOND = (1.0, 4.8)          # outside this range the synthesized speech is suspect
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
MODEL_RE = re.compile(r"^gemini-[a-z0-9][a-z0-9.\-]{1,60}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{20,200}$")
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
VOICES = {"A": "Samantha", "B": "Daniel"}


class LessonError(Exception):
    def __init__(self, status: int, msg: str, checks: list[dict] | None = None):
        super().__init__(msg)
        self.status = status
        self.msg = msg
        self.checks = checks or []


def lessons_dir():
    return config.DATA_DIR / "lessons"


def key_file():
    return config.DATA_DIR / "secrets" / "gemini.key"


def key_source() -> str | None:
    """Where the Gemini key comes from. The environment wins over the file saved from the settings page."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "env"
    return "file" if key_file().is_file() else None


def gemini_key() -> str | None:
    k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if k:
        return k.strip()
    try:
        return key_file().read_text().strip() or None
    except OSError:
        return None


def save_key(key: str) -> None:
    key = key.strip()
    if not KEY_RE.match(key):
        raise LessonError(400, "API キーの形式が正しくありません（英数字・- _ のみ、20文字以上）")
    d = key_file().parent
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = d / "gemini.key.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    os.replace(tmp, key_file())


def delete_key() -> bool:
    try:
        key_file().unlink()
        return True
    except FileNotFoundError:
        return False


def gemini_model() -> str:
    m = db.profile()["settings"].get("gemini_model") or os.environ.get("LISTENFLOW_GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    return m if MODEL_RE.match(m) else DEFAULT_GEMINI_MODEL


def engine_mode() -> str:
    m = db.profile()["settings"].get("lesson_engine", "auto")
    return m if m in ("auto", "gemini", "local") else "auto"


def effective_engine(mode: str | None = None) -> str:
    mode = mode or engine_mode()
    if mode == "auto":
        return "gemini" if gemini_key() else "local"
    return mode


def engine_status() -> dict:
    ok, msg = tts.available()
    key = gemini_key()
    return {"engine": effective_engine(), "mode": engine_mode(), "gemini_configured": bool(key),
            "key_source": key_source(), "key_hint": f"…{key[-4:]}" if key else None,
            "gemini_model": gemini_model(), "tts_available": ok, "tts_message": msg}


def _gemini_error(e: Exception) -> LessonError:
    if isinstance(e, urllib.error.HTTPError):
        msg = {400: "API キーまたはリクエストが無効です", 401: "API キーが無効です", 403: "API キーが無効か、Gemini API が有効になっていません",
               404: "モデルが見つかりません", 429: "利用上限に達しました（しばらく待つか、Google AI Studio で上限を確認）"}
        return LessonError(502, f"Gemini エラー {e.code}: {msg.get(e.code, 'サーバーエラー')}")
    return LessonError(502, f"Gemini に接続できませんでした: {type(e).__name__}")


def test_gemini(timeout: float = 15.0) -> dict:
    """Check the key and model with a metadata request (models.get), which does not consume generation quota."""
    key = gemini_key()
    if not key:
        raise LessonError(400, "Gemini の API キーが設定されていません")
    model = gemini_model()
    req = urllib.request.Request(f"{GEMINI_API}/{model}", headers={"x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        raise _gemini_error(e) from e
    methods = data.get("supportedGenerationMethods") or []
    if methods and "generateContent" not in methods:
        raise LessonError(400, f"{model} は文章生成（generateContent）に対応していません")
    return {"ok": True, "model": model, "display_name": data.get("displayName") or model}


# --- target words ----------------------------------------------------------------------

def select_targets(n: int = 3) -> list[Word]:
    """Weak words first (lowest ability in any modality), then untested words at the learner's tier."""
    lib = vocab_library()
    rows = db.q("SELECT * FROM user_vocab")
    scored = []
    for r in rows:
        if r["word_id"] not in lib.words or vocab.status_of(r) == "acquired":
            continue
        vals = [r[f"{m}_mastery"] for m in vocab.MODALITIES if r[f"{m}_n"]]
        if vals:
            scored.append((min(vals), random.random(), r["word_id"]))  # ties broken at random, not alphabetically
    scored.sort()
    out = [lib.words[w] for _, _, w in scored[:n]]
    if len(out) < n:
        tier = vocab.estimated_tier()
        known = {r["word_id"] for r in rows}
        fresh = [w for w in lib.by_tier(tier) if w.id not in known and w not in out]
        random.shuffle(fresh)
        out += fresh[: n - len(out)]
    return out


# --- script generation -------------------------------------------------------------------

OPENERS = {
    "office": ("Do you have a minute? I have a few updates.", "Sure. What's going on?"),
    "meeting": ("Before the meeting starts, can we go over a few things?", "Of course. Go ahead."),
    "hr": ("Thanks for coming in. I wanted to talk about the team.", "No problem. What did you want to discuss?"),
    "finance": ("I just got back from the finance meeting.", "Oh, how did it go?"),
    "sales": ("Do you have time to talk about our customers?", "Sure, I'm free right now."),
    "travel": ("I'm still planning the business trip.", "Great. Is there anything I can help with?"),
    "shipping": ("Have you heard from the warehouse today?", "Not yet. Is there a problem?"),
    "facilities": ("I have some news about the building.", "Really? What is it?"),
    "marketing": ("I'd like to share some news from the marketing team.", "Sure, I'd love to hear it."),
    "manufacturing": ("There's an update from the factory.", "Okay. What did they say?"),
}
REACTIONS = ["I see. That makes sense.", "Okay. I'll let the rest of the team know.",
             "Good to know. Thanks for telling me.", "Really? I didn't know that.",
             "Got it. I'll make a note of that.", "That's helpful. Thank you."]
CLOSINGS = [("Let's talk again after lunch.", "Sounds good. See you then."),
            ("I'll send you an email with the details.", "Perfect. Thanks.")]


def local_script(targets: list[Word], rng: random.Random) -> dict:
    """Template dialogue: each target word appears in its (validated) example sentence."""
    first = targets[0]
    o1, o2 = OPENERS.get(first.topic, OPENERS["office"])
    lines = [["A", o1], ["B", o2]]
    reactions = rng.sample(REACTIONS, len(targets))
    for i, w in enumerate(targets):
        spk, other = ("A", "B") if i % 2 == 0 else ("B", "A")
        lines.append([spk, w.example])
        lines.append([other, reactions[i]])
    c1, c2 = rng.choice(CLOSINGS)
    lines += [["A", c1], ["B", c2]]
    return {"topic": first.topic, "lines": lines, "engine": "local"}


def _gemini_prompt(targets: list[Word], level: int) -> str:
    words = ", ".join(w.lemma for w in targets)
    return (
        "Write a short, natural workplace conversation in English like TOEIC Listening Part 3.\n"
        f"- Two speakers, A and B, 6 to 10 lines in total, each line under 25 words.\n"
        f"- Use EACH of these words at least once, spelled exactly as given (inflections like -s/-ed/-ing are OK): {words}.\n"
        f"- Difficulty: CEFR around {['A2', 'B1', 'B1', 'B2', 'C1'][max(0, min(4, level - 1))]}. Plain spoken English, no names of real companies.\n"
        "- No stage directions, no speaker names inside the text, no non-English characters.\n"
        'Return JSON only: {"topic": "<2-4 words>", "dialogue": [{"speaker": "A", "text": "..."}]}'
    )


def gemini_script(targets: list[Word], level: int, timeout: float = 40.0) -> dict:
    key = gemini_key()
    if not key:
        raise LessonError(503, "Gemini の API キーが設定されていません")
    url = f"{GEMINI_API}/{gemini_model()}:generateContent"
    body = {"contents": [{"parts": [{"text": _gemini_prompt(targets, level)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        raise _gemini_error(e) from e
    return parse_gemini_response(data)


def _plain(text: str) -> str:
    """Normalise typographic punctuation that the speech engine and the scorer treat as noise."""
    for a, b in (("\u2014", ", "), ("\u2013", "-"), ("\u2026", "..."), ("\u2019", "'"), ("\u2018", "'"),
                 ("\u201c", '"'), ("\u201d", '"'), ("\u00a0", " ")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def parse_gemini_response(data: dict) -> dict:
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
        obj = json.loads(text)
        lines = [[str(x["speaker"]).strip().upper()[:1], _plain(str(x["text"]))] for x in obj["dialogue"]]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise LessonError(502, "Gemini の応答を読み取れませんでした") from e
    return {"topic": str(obj.get("topic", ""))[:40], "lines": lines, "engine": "gemini"}


# --- questions (generated mechanically so that exactly one answer is right) -------------------------

def make_questions(targets: list[Word], script_text: str, rng: random.Random) -> list[dict]:
    lib = vocab_library()
    qs = []
    for i, w in enumerate(targets):
        surface = w.appears_in(script_text) or w.lemma
        if i % 2 == 0:
            others = [o for o in lib.words.values() if o.id != w.id and not o.appears_in(script_text)
                      and abs(o.tier - w.tier) <= 1 and o.pos == w.pos]
            rng.shuffle(others)
            opts = [w.lemma] + [o.lemma for o in others[:3]]
            rng.shuffle(opts)
            qs.append({"type": "vocab-heard", "q": "会話の中で使われていた語はどれ？", "choices": opts,
                       "answer": opts.index(w.lemma), "explain": f"{surface}（{w.meaning}）", "word": w.id})
        else:
            ds = vocab._distractors(w, 3, rng)
            opts = [w.meaning] + [d.meaning for d in ds]
            rng.shuffle(opts)
            qs.append({"type": "vocab-meaning", "q": f"会話に出てきた「{surface}」の意味は？", "choices": opts,
                       "answer": opts.index(w.meaning), "explain": w.example, "word": w.id})
    return qs


# --- quality checks ---------------------------------------------------------------------------

def check_script(script: dict, targets: list[Word]) -> list[dict]:
    lines = script.get("lines") or []
    text = " ".join(t for _, t in lines)
    checks = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("台詞の数（4〜14行）", 4 <= len(lines) <= 14, f"{len(lines)}行")
    add("話者は A / B", all(s in ("A", "B") for s, _ in lines) and {s for s, _ in lines} == {"A", "B"})
    add("1行の長さ（1〜40語）", all(1 <= len(t.split()) <= 40 for _, t in lines))
    add("英語のみ", not re.search(r"[^\x20-\x7E’‘“”]", text))
    missing = [w.lemma for w in targets if not w.appears_in(text)]
    add("指定した単語がすべて含まれる", not missing, ", ".join(missing))
    return checks


def check_questions(qs: list[dict], script_text: str) -> list[dict]:
    lib = vocab_library()
    problems = []
    for q in qs:
        if len(q["choices"]) != 4 or len(set(q["choices"])) != 4 or not 0 <= q["answer"] < 4:
            problems.append(q["q"])
            continue
        if q["type"] == "vocab-heard":
            present = [c for c in q["choices"] if lib.words.get(c) and lib.words[c].appears_in(script_text)]
            if present != [q["choices"][q["answer"]]]:
                problems.append(q["q"])
        if q["type"] == "vocab-meaning":
            target = lib.words[q["word"]]
            wrong = [w for w in lib.words.values() if w.meaning in q["choices"] and w.id != target.id]
            if any(vocab.overlaps(target, w) for w in wrong):
                problems.append(q["q"])
    return [{"check": "正解が1つだけの問題", "ok": not problems, "detail": " / ".join(problems)}]


def check_audio(res: dict, lines: list) -> list[dict]:
    dur = res["duration"]
    rates = []
    for seg, (_, text) in zip(res["segments"], lines):
        secs = max(0.05, seg["end"] - seg["start"])
        rates.append(len(text.split()) / secs)
    lo, hi = WORDS_PER_SECOND
    odd = [i for i, r in enumerate(rates) if not lo <= r <= hi]
    return [
        {"check": f"音声の長さ（{MIN_SECONDS:.0f}〜{MAX_SECONDS:.0f}秒）", "ok": MIN_SECONDS <= dur <= MAX_SECONDS, "detail": f"{dur:.1f}秒"},
        {"check": "全ての台詞に音声がある", "ok": len(res["segments"]) == len(lines), "detail": ""},
        {"check": "発話速度が自然な範囲", "ok": not odd,
         "detail": "" if not odd else f"{len(odd)}行が範囲外（{', '.join(f'{rates[i]:.1f}語/秒' for i in odd)}）"},
    ]


# Calibrated on the 55 bundled clips (base.en): WER median 1.4%, p90 5.3%, max 24% (a 2-minute podcast);
# mean word probability >= 0.88; share of words under 0.3 <= 8.9%. Lessons are short dialogues, so the
# limits below leave margin for real speech while rejecting audio that does not match its script.
ASR_MAX_WER = 0.15
ASR_MIN_MEAN_PROB = 0.80
ASR_LOW_PROB, ASR_MAX_LOW_SHARE = 0.3, 0.15


def check_asr(res: dict, lines: list, targets: list[Word]) -> list[dict]:
    """Transcribe the synthesized lesson and compare it with the script. Not available -> reported as skipped."""
    ok, msg = asr.available()
    if not ok:
        return [{"check": "音声認識による照合", "ok": True, "skipped": True, "detail": f"未実施: {msg}"}]
    try:
        r = asr.transcribe([config.AUDIO_DIR / res["audio"]], timeout=180)[0]
    except asr.AsrError as e:
        return [{"check": "音声認識による照合", "ok": False, "detail": str(e)}]
    return asr_checks(r, lines, targets)


def asr_checks(r: dict, lines: list, targets: list[Word]) -> list[dict]:
    ref = asr.tokens(" ".join(t for _, t in lines))
    hyp = asr.tokens(r["text"])
    err = asr.wer(ref, hyp)
    heard_text = " ".join(hyp)
    missing = [w.lemma for w in targets if not w.appears_in(heard_text)]
    probs = [w["probability"] for w in r["words"]]
    mean = sum(probs) / len(probs) if probs else 0.0
    low = sum(p < ASR_LOW_PROB for p in probs) / len(probs) if probs else 1.0
    return [
        {"check": f"音声と英文が一致（音声認識の単語誤り率 {ASR_MAX_WER:.0%} 以下）", "ok": err <= ASR_MAX_WER,
         "detail": f"誤り率 {err:.1%}"},
        {"check": "指定語が音声から聞き取れる", "ok": not missing,
         "detail": "" if not missing else "認識されなかった語: " + ", ".join(missing)},
        {"check": "発音が明瞭（認識の確信度）", "ok": mean >= ASR_MIN_MEAN_PROB and low <= ASR_MAX_LOW_SHARE,
         "detail": f"平均 {mean:.2f}・低確信の語 {low:.0%}"},
    ]


# --- main entry -------------------------------------------------------------------------------

def cache_key(targets: list[Word], level: int, engine: str) -> str:
    raw = json.dumps([sorted(w.id for w in targets), level, engine, GENERATOR_VERSION])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _remove_audio(res: dict | None) -> None:
    if not res:
        return
    for rel in [res.get("audio")] + [s.get("audio") for s in res.get("segments", [])]:
        if rel:
            (config.AUDIO_DIR / rel).unlink(missing_ok=True)


def generate(word_ids: list[str] | None = None, engine: str | None = None, seed: int | None = None) -> dict:
    lib = vocab_library()
    ok, msg = tts.available()
    if not ok:
        raise LessonError(503, msg)
    if word_ids:
        unknown = [w for w in word_ids if w not in lib.words]
        if unknown:
            raise LessonError(400, f"辞書にない単語です: {', '.join(unknown)}")
        targets = [lib.words[w] for w in dict.fromkeys(word_ids)][:4]
    else:
        targets = select_targets(3)
    if not targets:
        raise LessonError(400, "対象の単語がありません")
    mode = engine or engine_mode()
    engine = effective_engine(mode)
    if engine == "gemini" and not gemini_key():
        raise LessonError(503, "Gemini の API キーが設定されていません")
    level = db.profile()["level"]
    rng = random.Random(seed)

    def cached(eng: str):
        row = db.q1("SELECT * FROM audio_lessons WHERE cache_key=?", (cache_key(targets, level, eng),))
        if row and (lessons_dir() / f"{row['id']}.json").exists():
            return {**lesson_public(row), "cached": True}
        return None

    def write(eng: str):
        script = gemini_script(targets, level) if eng == "gemini" else local_script(targets, rng)
        checks = check_script(script, targets)
        text = " ".join(t for _, t in script["lines"])
        qs = make_questions(targets, text, rng)
        checks += check_questions(qs, text)
        if not all(c["ok"] for c in checks):
            raise LessonError(422, "品質チェックに通らなかったため、教材を採用しませんでした", checks)
        return script, checks, text, qs

    if hit := cached(engine):
        return hit
    fallback = None
    try:
        if engine == "gemini":
            try:
                script, checks, text, qs = write("gemini")
            except LessonError as e:
                if e.status != 422:
                    raise
                script, checks, text, qs = write("gemini")   # one retry: Gemini sometimes drops a word
        else:
            script, checks, text, qs = write("local")
    except LessonError as e:
        # "auto" never leaves the learner without a lesson: Gemini failures fall back to the local generator.
        if engine != "gemini" or mode != "auto":
            raise
        fallback = e.msg
        engine = "local"
        if hit := cached(engine):
            return {**hit, "fallback": fallback}
        script, checks, text, qs = write("local")
    if fallback:
        checks.insert(0, {"check": "生成エンジン", "ok": True, "detail": f"Gemini が使えなかったためローカル生成に切り替え（{fallback}）"})
    key = cache_key(targets, level, engine)

    lid = f"g-{key[:10]}"
    raw = {
        "id": lid, "level": level, "pool": "train", "kind": "dialogue",
        "title": "語彙リスニング: " + " / ".join(w.lemma for w in targets),
        "voices": dict(VOICES), "lines": script["lines"], "questions": qs,
        "vocab": [[w.appears_in(text) or w.lemma, w.meaning] for w in targets],
        "target_words": [w.id for w in targets], "engine": script["engine"], "topic": script.get("topic"),
    }
    res = None
    try:
        res = tts.synthesize_one(raw)
        audio_checks = check_audio(res, script["lines"])
        if all(c["ok"] for c in audio_checks):
            audio_checks += check_asr(res, script["lines"], targets)
    except Exception as e:  # noqa: BLE001 - any synthesis failure rejects the lesson
        audio_checks = [{"check": "音声合成", "ok": False, "detail": str(e)[:200]}]
    checks += audio_checks
    if not all(c["ok"] for c in audio_checks):
        _remove_audio(res)
        with tts._manifest_lock:
            m = tts.load_manifest()
            if m.pop(lid, None) is not None:
                tts.save_manifest(m)
        raise LessonError(422, "音声の品質チェックに通らなかったため、教材を採用しませんでした", checks)

    lessons_dir().mkdir(parents=True, exist_ok=True)
    (lessons_dir() / f"{lid}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1))
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO audio_lessons (id, cache_key, created_at, engine, targets, level, checks)"
                     " VALUES (?,?,?,?,?,?,?)",
                     (lid, key, db.now_iso(), script["engine"], json.dumps(raw["target_words"]), level,
                      json.dumps(checks, ensure_ascii=False)))
    reload_library()
    return {**lesson_public(db.q1("SELECT * FROM audio_lessons WHERE id=?", (lid,))), "cached": False, "fallback": fallback}


def lesson_public(row) -> dict:
    lib = vocab_library()
    targets = json.loads(row["targets"])
    heard = db.q1("SELECT count FROM exposures WHERE kind='clip' AND item_id=?", (row["id"],))
    return {"id": row["id"], "created_at": row["created_at"], "engine": row["engine"], "level": row["level"],
            "targets": [{"id": t, "lemma": lib.words[t].lemma, "meaning": lib.words[t].meaning}
                        for t in targets if t in lib.words],
            "checks": json.loads(row["checks"]), "heard": bool(heard)}


def list_lessons() -> list[dict]:
    return [lesson_public(r) for r in db.q("SELECT * FROM audio_lessons ORDER BY created_at DESC")
            if (lessons_dir() / f"{r['id']}.json").exists()]


def delete_lesson(lid: str) -> bool:
    p = lessons_dir() / f"{lid}.json"
    if not lid.startswith("g-") or not p.exists():
        return False
    p.unlink()
    with tts._manifest_lock:
        m = tts.load_manifest()
        _remove_audio(m.pop(lid, None))
        tts.save_manifest(m)
    with db.tx() as conn:
        conn.execute("DELETE FROM audio_lessons WHERE id=?", (lid,))
    reload_library()
    return True


def unheard_count() -> int:
    heard = {r["item_id"] for r in db.q("SELECT item_id FROM exposures WHERE kind='clip'")}
    return sum(1 for r in db.q("SELECT id FROM audio_lessons") if r["id"] not in heard
               and (lessons_dir() / f"{r['id']}.json").exists())
