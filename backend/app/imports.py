"""Import external listening audio (e.g. a NotebookLM Audio Overview) as practice material.

upload -> ffprobe validation -> faster-whisper transcription with timestamps -> optional script alignment
(when the learner pastes the script, its wording is used and speech recognition only supplies timing)
-> split into ~90 s clips of 3-12 s dictation lines -> per-clip quality gate -> TOEIC words found in the
clip become targets with mechanically generated single-answer questions -> stored as user clips ("u-nlm-").

Runs in a background thread; the job row in `imports` records progress, results and errors.
"""
from __future__ import annotations

import difflib
import json
import random
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from . import asr, config, db, lessons, tts
from .content import reload_library
from .vocab_data import vocab_library

MAX_BYTES = 300 * 1024 * 1024
MAX_SECONDS = 60 * 60
MIN_SECONDS = 10
CLIP_TARGET, CLIP_MAX = 90.0, 140.0       # seconds per imported clip
LINE_MIN, LINE_MAX, LINE_MAX_WORDS = 3.0, 12.0, 30
SCRIPT_MAX_WER = 0.25                     # whole-file agreement required to trust a pasted script
CHUNK_MAX_WER = 0.20                      # per clip, with a script

_running: set[str] = set()
_lock = threading.Lock()


class ImportFailed(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status
        self.msg = msg


def imports_dir() -> Path:
    return config.DATA_DIR / "imports"


# --- jobs ----------------------------------------------------------------------------------

def _row(jid: str) -> dict | None:
    r = db.q1("SELECT * FROM imports WHERE id=?", (jid,))
    if not r:
        return None
    d = dict(r)
    d["info"] = json.loads(d["info"] or "{}")
    if d["status"] in ("queued", "processing") and jid not in _running:
        # the server restarted while this job was running
        _update(jid, status="failed", error="サーバーの再起動で処理が中断されました。もう一度取り込んでください")
        return _row(jid)
    return d


def _update(jid: str, **kw) -> None:
    if "info" in kw:
        kw["info"] = json.dumps(kw["info"], ensure_ascii=False)
    kw["updated_at"] = db.now_iso()
    cols = ", ".join(f"{k}=?" for k in kw)
    with db.tx() as conn:
        conn.execute(f"UPDATE imports SET {cols} WHERE id=?", (*kw.values(), jid))


def get_job(jid: str) -> dict | None:
    return _row(jid)


def list_jobs() -> list[dict]:
    return [j for j in (_row(r["id"]) for r in db.q("SELECT id FROM imports ORDER BY created_at DESC")) if j]


def probe(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type:format=duration",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
        info = json.loads(r.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
        raise ImportFailed(400, "音声ファイルを読み取れませんでした") from e
    if not info.get("streams"):
        raise ImportFailed(400, "音声が含まれていないファイルです")
    dur = float((info.get("format") or {}).get("duration") or 0)
    if not MIN_SECONDS <= dur <= MAX_SECONDS:
        raise ImportFailed(400, f"音声の長さは {MIN_SECONDS}秒〜{MAX_SECONDS // 60}分にしてください（{dur:.0f}秒）")
    return dur


def start(src: Path, filename: str, title: str, level: int, script: str | None) -> dict:
    """`src` is the uploaded file (moved into DATA_DIR/imports). Validates, then processes in the background."""
    ok, msg = asr.available()
    if not ok:
        src.unlink(missing_ok=True)
        raise ImportFailed(503, msg)
    try:
        dur = probe(src)
    except ImportFailed:
        src.unlink(missing_ok=True)
        raise
    jid = uuid.uuid4().hex[:10]
    imports_dir().mkdir(parents=True, exist_ok=True)
    dst = imports_dir() / f"{jid}{Path(filename).suffix.lower()[:6]}"
    shutil.move(str(src), dst)
    info = {"filename": filename[:120], "duration": round(dur, 1), "has_script": bool(script and script.strip()),
            "step": "待機中", "clips": [], "skipped": [], "checks": []}
    with db.tx() as conn:
        conn.execute("INSERT INTO imports (id, status, created_at, updated_at, title, level, source, info, error)"
                     " VALUES (?,?,?,?,?,?,?,?,NULL)",
                     (jid, "queued", db.now_iso(), db.now_iso(), title, level, "notebooklm", json.dumps(info, ensure_ascii=False)))
    _running.add(jid)
    threading.Thread(target=_run, args=(jid, dst, script), daemon=True).start()
    return get_job(jid)


def _run(jid: str, path: Path, script: str | None) -> None:
    try:
        with _lock:   # one transcription at a time; it is CPU-bound
            _process(jid, path, script)
    except ImportFailed as e:
        _update(jid, status="failed", error=e.msg)
    except Exception as e:  # noqa: BLE001 - report, never crash the server
        _update(jid, status="failed", error=f"取り込みに失敗しました: {type(e).__name__}: {str(e)[:160]}")
    finally:
        _running.discard(jid)
        path.unlink(missing_ok=True)   # the original upload is not kept; clips keep their own audio


# --- processing --------------------------------------------------------------------------------

def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *args], check=True, capture_output=True, timeout=600)


def _align_script(words: list[dict], script: str, checks: list) -> list[str]:
    """Match ASR words to the pasted script. Returns the script words and sets w["sidx"] on matched ASR words."""
    sw = script.split()
    s_norm = [" ".join(asr.tokens(w)) for w in sw]
    a_norm = [" ".join(asr.tokens(w["word"])) for w in words]
    err = asr.wer([t for t in s_norm if t], [t for t in a_norm if t])
    ok = err <= SCRIPT_MAX_WER
    checks.append({"check": f"台本と音声が一致（音声認識との差 {SCRIPT_MAX_WER:.0%} 以下）", "ok": ok, "detail": f"差 {err:.1%}"})
    if not ok:
        raise ImportFailed(422, f"貼り付けた台本と音声が一致しません（差 {err:.0%}）。台本なしで取り込むか、正しい台本を貼り付けてください")
    sm = difflib.SequenceMatcher(None, a_norm, s_norm, autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            words[blk.a + k]["sidx"] = blk.b + k
    return sw


def _apply_script(lines: list[dict], sw: list[str]) -> None:
    """Each line takes the script words from its first aligned word up to the next line's."""
    starts = []
    for ln in lines:
        idx = next(((w["sidx"] - j) for j, w in enumerate(ln["words"]) if w.get("sidx") is not None), None)
        starts.append(idx)
    starts[0] = 0
    for i in range(1, len(starts)):   # unaligned line: continue from the previous one; keep monotonic
        if starts[i] is None or starts[i] < starts[i - 1]:
            starts[i] = starts[i - 1]
    starts.append(len(sw))
    for i, ln in enumerate(lines):
        ln["text"] = " ".join(sw[starts[i]:starts[i + 1]]).strip()


def _lines(words: list[dict]) -> list[dict]:
    """Group words into dictation lines: end at sentence punctuation once >= LINE_MIN s, never beyond LINE_MAX."""
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        dur = cur[-1]["end"] - cur[0]["start"]
        nxt = words[i + 1] if i + 1 < len(words) else None
        sentence_end = w["word"].rstrip('"\')').endswith((".", "?", "!"))
        pause = nxt is not None and nxt["start"] - w["end"] > 0.6
        if (dur >= LINE_MIN and (sentence_end or pause)) or dur >= LINE_MAX or len(cur) >= LINE_MAX_WORDS or nxt is None:
            lines.append({"start": cur[0]["start"], "end": cur[-1]["end"], "words": cur,
                          "text": " ".join(x["word"] for x in cur).strip()})
            cur = []
    return [ln for ln in lines if ln["text"]]


def _chunks(lines: list[dict]) -> list[list[dict]]:
    out, cur = [], []
    for ln in lines:
        if cur and (ln["end"] - cur[0]["start"] > CLIP_MAX or (cur[-1]["end"] - cur[0]["start"] >= CLIP_TARGET
                                                              and cur[-1]["text"].rstrip().endswith((".", "?", "!")))):
            out.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        if out and cur[-1]["end"] - cur[0]["start"] < 20:   # a short tail joins the previous clip
            out[-1].extend(cur)
        else:
            out.append(cur)
    return out


def _chunk_quality(chunk: list[dict], has_script: bool, asr_words: list[dict]) -> tuple[bool, str]:
    t0, t1 = chunk[0]["start"], chunk[-1]["end"]
    probs = [w["probability"] for w in asr_words if t0 - 0.01 <= w["start"] <= t1 + 0.01]
    if not probs:
        return False, "音声を認識できませんでした"
    mean = sum(probs) / len(probs)
    low = sum(p < lessons.ASR_LOW_PROB for p in probs) / len(probs)
    if has_script:
        return True, f"確信度 平均 {mean:.2f}"
    ok = mean >= lessons.ASR_MIN_MEAN_PROB and low <= lessons.ASR_MAX_LOW_SHARE
    return ok, f"確信度 平均 {mean:.2f}・低確信の語 {low:.0%}"


def _targets(text: str, n: int = 3):
    """TOEIC words in this text, weakest for the learner first."""
    lib = vocab_library()
    found = [w for w in lib.words.values() if w.appears_in(text)]
    mastery = {r["word_id"]: min(v for v in (r["text_mastery"], r["audio_mastery"], r["context_mastery"]) if v is not None)
               for r in db.q("SELECT * FROM user_vocab")
               if any(v is not None for v in (r["text_mastery"], r["audio_mastery"], r["context_mastery"]))}
    found.sort(key=lambda w: (mastery.get(w.id, 0.5), -w.tier))
    return found[:n]


def _process(jid: str, path: Path, script: str | None) -> None:
    job = get_job(jid)
    info, title, level = job["info"], job["title"], job["level"]
    _update(jid, status="processing", info={**info, "step": "音声を変換しています"})
    work = imports_dir() / f"{jid}-work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        master = work / "master.wav"
        _ffmpeg("-i", str(path), "-ac", "1", "-ar", str(tts.SAMPLE_RATE), str(master))
        _update(jid, info={**info, "step": "文字起こししています（音声認識）"})
        r = asr.transcribe([master], timeout=max(600.0, info["duration"] * 2))[0]
        checks: list[dict] = []
        words = [dict(w) for w in r["words"] if w["word"].strip()]
        if len(words) < 10:
            raise ImportFailed(422, "英語の音声を認識できませんでした")
        has_script = bool(script and script.strip())
        sw = _align_script(words, script, checks) if has_script else None
        _update(jid, info={**info, "step": "教材に分割しています", "checks": checks})
        lines = _lines(words)
        if sw:
            _apply_script(lines, sw)
        chunks = _chunks([ln for ln in lines if ln["text"]])
        rng = random.Random(jid)
        (config.AUDIO_DIR / "clips").mkdir(parents=True, exist_ok=True)
        (config.AUDIO_DIR / "segments").mkdir(parents=True, exist_ok=True)
        made, skipped = [], []
        stamp = db.now().strftime("%y%m%d%H%M")
        for k, chunk in enumerate(chunks, 1):
            ok, detail = _chunk_quality(chunk, has_script, r["words"])
            t0, t1 = max(0.0, chunk[0]["start"] - 0.25), chunk[-1]["end"] + 0.35
            label = f"{int(t0 // 60)}:{int(t0 % 60):02d}〜{int(t1 // 60)}:{int(t1 % 60):02d}"
            if not ok:
                skipped.append({"part": k, "range": label, "reason": f"音声認識の確信度が低いため除外（{detail}）"})
                continue
            cid = f"u-nlm-{jid}-{k:02d}"
            text = " ".join(ln["text"] for ln in chunk)
            targets = _targets(text)
            qs = lessons.make_questions(targets, text, rng)
            if not all(c["ok"] for c in lessons.check_questions(qs, text)):
                qs, targets = [], []
            seg_meta = []
            for i, ln in enumerate(chunk):
                rel = f"segments/{cid}_{i}.mp3"
                a, b = max(0.0, ln["start"] - 0.15), ln["end"] + 0.25
                _ffmpeg("-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(master), "-codec:a", "libmp3lame", "-q:a", "4",
                        str(config.AUDIO_DIR / rel))
                seg_meta.append({"audio": rel, "start": round(ln["start"] - t0, 3), "end": round(ln["end"] - t0, 3)})
            clip_rel = f"clips/{cid}.mp3"
            _ffmpeg("-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-i", str(master), "-codec:a", "libmp3lame", "-q:a", "4",
                    str(config.AUDIO_DIR / clip_rel))
            raw = {
                "id": cid, "level": level, "pool": "train", "kind": "podcast",
                "title": f"{title}（{k}/{len(chunks)}）", "voices": {"A": "NotebookLM"},
                "lines": [["A", ln["text"]] for ln in chunk], "questions": qs,
                "vocab": [[w.appears_in(text) or w.lemma, w.meaning] for w in targets],
                "target_words": [w.id for w in targets], "import_id": jid, "source_kind": "notebooklm",
                "transcript_source": "script" if script and script.strip() else "asr",
            }
            with tts._manifest_lock:
                m = tts.load_manifest()
                m[cid] = {"audio": clip_rel, "duration": round(t1 - t0, 3), "segments": seg_meta, "hash": f"import-{jid}"}
                tts.save_manifest(m)
            config.USER_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
            (config.USER_CONTENT_DIR / f"{cid}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1))
            made.append({"id": cid, "range": label, "lines": len(chunk), "questions": len(qs),
                         "words": [w.lemma for w in targets], "quality": detail})
        reload_library()
        info = {**info, "step": "完了", "clips": made, "skipped": skipped, "checks": checks, "asr_model": asr.MODEL,
                "stamp": stamp}
        if not made:
            _update(jid, status="failed", info=info, error="品質チェックに通る部分がありませんでした")
        else:
            _update(jid, status="done", info=info)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- removal -----------------------------------------------------------------------------------

def remove_user_clip(cid: str) -> bool:
    p = config.USER_CONTENT_DIR / f"{cid}.json"
    if not cid.startswith("u-") or not p.exists():
        return False
    p.unlink()
    with tts._manifest_lock:
        manifest = tts.load_manifest()
        entry = manifest.pop(cid, None)
        if entry:
            for rel in [entry.get("audio")] + [s.get("audio") for s in entry.get("segments", [])]:
                if rel:
                    (config.AUDIO_DIR / rel).unlink(missing_ok=True)
            tts.save_manifest(manifest)
    return True


def delete_job(jid: str) -> bool:
    job = get_job(jid)
    if not job:
        return False
    if jid in _running:
        raise ImportFailed(409, "処理中の取り込みは削除できません")
    for c in job["info"].get("clips", []):
        remove_user_clip(c["id"])
    with db.tx() as conn:
        conn.execute("DELETE FROM imports WHERE id=?", (jid,))
    reload_library()
    return True
