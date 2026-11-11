"""Context → Sentence → Transfer.

A training cycle starts with a conversation heard for the first time (context), finds the line the learner
could not hear, explains it (English, Japanese, scene, key expression, structure, what was mis-heard), asks
whether the line is understood *as text* to separate sound from vocabulary/grammar problems, and finally plays
a different sentence, in a different situation and voice, that uses the same expression (transfer).

Notes per line live in content/notes/<content file>.json (built-in material, written for ListenFlow, CC0) or in
a generated lesson's own "notes". Transfer audio: media/transfer/<hash>.mp3, built by scripts.build_audio.

Records (table sentence_studies) keep the first-listen result, the text check and the transfer result apart,
so nothing seen after the explanation is counted as first-listen comprehension.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import config, db

MAC_VOICES = ["Samantha", "Daniel", "Karen", "Moira", "Tessa", "Rishi"]
STUDY_BELOW = 0.9          # a line dictated below this accuracy becomes the cycle's study sentence
ERROR_TYPES = ("phonetic", "vocabulary", "grammar", "meaning", "unknown")
ERROR_LABEL = {"phonetic": "音声知覚・語認識", "vocabulary": "語彙不足", "grammar": "構文理解",
               "meaning": "統合理解・処理速度", "unknown": "不明"}
NEXT_STEP = {"phonetic": "音の区間訓練（聞き分け・ディクテーション）", "vocabulary": "単語＋例文の復習",
             "grammar": "短文の意味理解", "meaning": "会話単位の練習", "unknown": "判断材料を増やす"}
TEXT_CHECKS = ("clear", "unknown_words", "structure")
MIN_TREND = 8              # classified studies needed before naming a dominant cause


@dataclass(frozen=True)
class Transfer:
    id: str
    clip_id: str
    index: int
    level: int
    text: str
    form: str
    ja: str
    q: str
    choices: tuple[str, ...]
    answer: int
    voice: str

    @property
    def audio_key(self) -> str:
        return hashlib.sha1(json.dumps([self.text, self.voice, config.LEVEL_RATE.get(self.level, 175)]).encode()).hexdigest()[:12]


def _ascii(text: str) -> bool:
    return not re.search(r"[^\x20-\x7E]", text)


def _has(text: str, expr: str) -> bool:
    return bool(expr) and re.search(rf"(?<![A-Za-z]){re.escape(expr.strip())}(?![A-Za-z])", text, re.I) is not None


def validate_line(line_text: str, note: dict) -> list[str]:
    """Problems with one line's note (empty list = usable)."""
    errs = []
    if not isinstance(note, dict):
        return ["note must be an object"]
    if not str(note.get("ja") or "").strip():
        errs.append("ja missing")
    key = note.get("key")
    if key is not None and not (isinstance(key, dict) and _has(line_text, str(key.get("expr", "")))):
        errs.append(f"key.expr not in line: {key}")
    tr = note.get("transfer")
    if tr is not None:
        errs += [f"transfer: {e}" for e in validate_transfer(tr, line_text)]
        if key is None:
            errs.append("transfer without key")
    return errs


def validate_transfer(tr: dict, line_text: str | None = None) -> list[str]:
    errs = []
    if not isinstance(tr, dict):
        return ["must be an object"]
    text = str(tr.get("text") or "")
    if not text or not _ascii(text):
        errs.append("text must be plain ASCII English")
    if not 4 <= len(text.split()) <= 24:
        errs.append("text length 4-24 words")
    if not _has(text, str(tr.get("form", ""))):
        errs.append("form not in text")
    if line_text and text.strip().lower() == line_text.strip().lower():
        errs.append("same as the practice line")
    ch = tr.get("choices") or []
    if len(ch) != 4 or len({str(c) for c in ch}) != 4 or not all(str(c).strip() for c in ch):
        errs.append("needs 4 distinct choices")
    if not isinstance(tr.get("answer"), int) or not 0 <= tr["answer"] < len(ch):
        errs.append("answer out of range")
    if not str(tr.get("ja") or "").strip() or not str(tr.get("q") or "").strip():
        errs.append("ja and q required")
    return errs


def _pick_voice(tid: str, avoid: set[str]) -> str:
    pool = [v for v in MAC_VOICES if v not in avoid] or MAC_VOICES
    return pool[int(hashlib.sha1(tid.encode()).hexdigest(), 16) % len(pool)]


class NoteBank:
    """Notes for every clip in the library: builtin note files plus the notes inside generated lessons."""

    def __init__(self) -> None:
        from .content import raw_clips
        self.notes: dict[str, dict] = {}       # clip id -> {"scene": str, "lines": [note | None]}
        self.transfers: dict[str, Transfer] = {}
        self.errors: list[str] = []
        files: dict[str, dict] = {}
        ndir = config.CONTENT_DIR / "notes"
        if ndir.exists():
            for p in sorted(ndir.glob("*.json")):
                files.update(json.loads(p.read_text()))
        for raw, _src in raw_clips():
            data = raw.get("notes") or files.get(raw["id"])
            if data:
                self._add(raw, data)
        self.manifest = load_manifest()

    def _add(self, raw: dict, data: dict) -> None:
        cid = raw["id"]
        lines = raw.get("lines") or []
        notes = data.get("lines") or []
        if len(notes) != len(lines):
            self.errors.append(f"{cid}: {len(notes)} notes for {len(lines)} lines")
            return
        voices = set((raw.get("voices") or {}).values())
        out = []
        for i, ((_spk, text), note) in enumerate(zip(lines, notes)):
            if note is None:
                out.append(None)
                continue
            errs = validate_line(text, note)
            if errs:
                self.errors.append(f"{cid}#{i}: " + "; ".join(errs))
                # an unusable transfer does not throw away a good translation
                if note.get("ja") and not any(e.startswith(("ja", "key")) for e in errs):
                    note = {**note, "transfer": None}
                else:
                    out.append(None)
                    continue
            out.append(note)
            tr = note.get("transfer")
            if tr:
                tid = f"{cid}#{i}"
                self.transfers[tid] = Transfer(tid, cid, i, int(raw.get("level", 3)), tr["text"].strip(), tr["form"],
                                               tr["ja"], tr["q"], tuple(tr["choices"]), tr["answer"],
                                               _pick_voice(tid, voices))
        self.notes[cid] = {"scene": str(data.get("scene") or ""), "lines": out}

    def note(self, clip_id: str, index: int) -> dict | None:
        n = self.notes.get(clip_id)
        if not n or index >= len(n["lines"]):
            return None
        return n["lines"][index]

    def scene(self, clip_id: str) -> str:
        return (self.notes.get(clip_id) or {}).get("scene", "")

    def audio(self, t: Transfer) -> str | None:
        m = self.manifest.get(t.id) or {}
        return m.get("audio") if m.get("hash") == t.audio_key else None

    def missing_audio(self) -> list[str]:
        return [t.id for t in self.transfers.values()
                if not self.audio(t) or not (config.AUDIO_DIR / self.audio(t)).exists()]


_bank: NoteBank | None = None
_bank_lock = threading.Lock()


def bank() -> NoteBank:
    global _bank
    with _bank_lock:
        if _bank is None:
            _bank = NoteBank()
        return _bank


def reload_bank() -> NoteBank:
    global _bank
    with _bank_lock:
        _bank = NoteBank()
        return _bank


def manifest_path() -> Path:
    return config.AUDIO_DIR / "transfer_manifest.json"


def load_manifest() -> dict:
    p = manifest_path()
    return json.loads(p.read_text()) if p.exists() else {}


_build_lock = threading.Lock()


def build_audio(force: bool = False, workers: int = 3, log=print, only: set[str] | None = None) -> int:
    """Synthesise transfer sentences (macOS `say`). Returns how many were built."""
    from . import tts
    config.ensure_dirs()
    with _build_lock:
        b = NoteBank()
        manifest = load_manifest()
        todo = [t for t in b.transfers.values()
                if (only is None or t.clip_id in only)
                and (force or (manifest.get(t.id) or {}).get("hash") != t.audio_key
                     or not (config.AUDIO_DIR / (manifest.get(t.id) or {}).get("audio", "missing")).exists())]
        (config.AUDIO_DIR / "transfer").mkdir(parents=True, exist_ok=True)

        def one(t: Transfer):
            rel = f"transfer/{t.audio_key}.mp3"
            tts._say_to_mp3(t.text, t.voice, config.LEVEL_RATE.get(t.level, 175), config.AUDIO_DIR / rel)
            return t.id, {"audio": rel, "hash": t.audio_key}

        if todo:
            log(f"  transfer sentences: {len(todo)} to synthesize")
            with ThreadPoolExecutor(workers) as ex:
                for tid, entry in ex.map(one, todo):
                    manifest[tid] = entry
            manifest_path().write_text(json.dumps(manifest, indent=1))
    reload_bank()
    return len(todo)


# --- the explanation -----------------------------------------------------------------------------------------

def listening_notes(tokens: list[dict]) -> list[dict]:
    """What was mis-heard in the dictation, grouped by sound pattern (from the scorer's categories)."""
    from .scoring import SKILLS
    out: dict[str, list[str]] = {}
    for t in tokens or []:
        if t.get("status") in ("missed", "wrong"):
            out.setdefault(t.get("category") or "vocabulary", []).append(t["text"])
    return [{"category": c, "label": SKILLS.get(c, c), "words": ws} for c, ws in out.items()]


def study_payload(seg, tokens: list[dict], accuracy: float | None) -> dict:
    from .content import library
    b = bank()
    clip = library().clips[seg.clip_id]
    note = b.note(seg.clip_id, seg.index) or {}
    prev = clip.segments[seg.index - 1] if seg.index > 0 else None
    words = []
    if clip.targets:     # generated lessons: the target words' meanings are known even without a note
        from .vocab_data import vocab_library
        vl = vocab_library().words
        for wid in clip.targets:
            w = vl.get(wid)
            if w and w.appears_in(seg.text):
                words.append({"expr": w.appears_in(seg.text), "ja": w.meaning})
    return {
        "type": "study",
        "public": {
            "type": "study", "level": seg.level, "clip_title": clip.title,
            "segment": {"id": seg.id, "audio": seg.audio, "speaker": seg.speaker},
            "text": seg.text, "words_in_text": seg.text.split(),
            "ja": note.get("ja"), "scene": b.scene(seg.clip_id) or None,
            "key": note.get("key") or (words[0] if words else None), "grammar": note.get("grammar"),
            "listening": listening_notes(tokens), "accuracy": accuracy,
            "context": [{"speaker": prev.speaker, "text": prev.text, "ja": (b.note(seg.clip_id, prev.index) or {}).get("ja")}]
            if prev else [],
        },
        "secret": {"segment_id": seg.id},
    }


def classify(text_check: str | None, meaning_understood: bool | None) -> str:
    """The likely reason a line was not heard, from one study. One study is weak evidence; see trends()."""
    if text_check == "unknown_words":
        return "vocabulary"
    if text_check == "structure" or (text_check == "clear" and meaning_understood is False):
        return "grammar"
    if text_check == "clear":
        return "phonetic"      # understood when read, missed when heard
    return "unknown"


# --- transfer ------------------------------------------------------------------------------------------------

def transfer_for(conn, seg) -> Transfer | None:
    """The practice line's own transfer sentence if never heard; otherwise None (a heard one would test memory)."""
    t = bank().transfers.get(seg.id)
    if not t or not bank().audio(t):
        return None
    if conn.execute("SELECT 1 FROM exposures WHERE kind='transfer' AND item_id=?", (t.id,)).fetchone():
        return None
    return t


def transfer_payload(t: Transfer, practice_text: str) -> dict:
    rng = random.Random()
    order = list(range(4))
    rng.shuffle(order)
    return {
        "type": "transfer",
        "public": {"type": "transfer", "audio": bank().audio(t), "level": t.level, "q": t.q,
                   "choices": [t.choices[i] for i in order]},
        "secret": {"transfer_id": t.id, "answer": order.index(t.answer), "practice": practice_text},
    }


# --- records -------------------------------------------------------------------------------------------------

def start_record(conn, session_id: str, clip_id: str, context_score: float | None, first_listen: bool,
                 mode: str = "standard") -> str:
    sid = uuid.uuid4().hex[:12]
    conn.execute("INSERT INTO sentence_studies (id, session_id, created_at, date, clip_id, context_score, first_listen,"
                 " error_type, mode) VALUES (?,?,?,?,?,?,?,?,?)",
                 (sid, session_id, db.now_iso(), db.local_date(), clip_id, context_score, int(first_listen), "unknown", mode))
    return sid


def update_record(conn, sid: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE sentence_studies SET {cols} WHERE id=?", (*fields.values(), sid))


def trends(conn=None, window: int = 60) -> dict:
    """Shares of the estimated causes over the latest studies. A dominant cause is named only with enough data."""
    sql = ("SELECT error_type FROM sentence_studies WHERE text_check IS NOT NULL OR error_type='meaning'"
           " ORDER BY created_at DESC LIMIT ?")
    rows = (conn.execute(sql, (window,)).fetchall() if conn is not None else db.q(sql, (window,)))
    counts = {e: 0 for e in ERROR_TYPES}
    for r in rows:
        counts[r["error_type"] if r["error_type"] in counts else "unknown"] += 1
    n = sum(counts.values())
    known = {k: v for k, v in counts.items() if k != "unknown"}
    top = max(known, key=known.get) if n and max(known.values()) else None
    dominant = top if top and n >= MIN_TREND and known[top] / n >= 0.4 else None
    return {"n": n, "counts": counts, "labels": ERROR_LABEL, "dominant": dominant,
            "next_step": NEXT_STEP.get(dominant) if dominant else None, "min_n": MIN_TREND}


def transfer_rate(conn=None) -> dict:
    sql = "SELECT AVG(transfer_correct) s, COUNT(*) n FROM sentence_studies WHERE transfer_correct IS NOT NULL"
    r = conn.execute(sql).fetchone() if conn is not None else db.q1(sql)
    return {"value": round(r["s"], 3) if r["n"] else None, "n": r["n"]}
