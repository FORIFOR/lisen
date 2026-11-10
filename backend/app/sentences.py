"""Single-sentence comprehension items ("構文理解"): hear one sentence, answer a question whose answer
depends on its grammar (negation, tense, condition, passive, comparison, reference, modality, contrast).

Dataset: content/sentences.json (CC0, written for ListenFlow). Audio: media/sentences/<hash>.mp3, built by
scripts.build_audio. File names are hashes so the URL does not reveal the sentence.
"""
from __future__ import annotations

import hashlib
import json
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import config

FOCUS_LABEL = {"negation": "否定", "tense": "時制", "comparison": "比較", "passive": "受け身", "condition": "条件・仮定",
               "time": "時の表現", "number": "数", "reference": "指示・関係詞", "modal": "助動詞", "contrast": "逆接・対比"}


@dataclass(frozen=True)
class Sentence:
    id: str
    level: int
    focus: str
    voice: str
    text: str
    q: str
    choices: tuple[str, ...]
    answer: int
    explain: str

    @property
    def audio_key(self) -> str:
        return hashlib.sha1(json.dumps([self.text, self.voice, config.LEVEL_RATE.get(self.level, 175)]).encode()).hexdigest()[:12]


def validate(data: dict) -> list[str]:
    errs = []
    if (data.get("dataset") or {}).get("license") != "CC0-1.0":
        errs.append("dataset.license must be CC0-1.0")
    seen = set()
    for i, it in enumerate(data.get("items", [])):
        where = it.get("id", f"#{i}")
        if it.get("id") in seen:
            errs.append(f"{where}: duplicate id")
        seen.add(it.get("id"))
        if it.get("level") not in (1, 2, 3, 4, 5):
            errs.append(f"{where}: level must be 1-5")
        if it.get("focus") not in FOCUS_LABEL:
            errs.append(f"{where}: unknown focus {it.get('focus')}")
        ch = it.get("choices") or []
        if len(ch) != 4 or len(set(ch)) != 4:
            errs.append(f"{where}: needs 4 distinct choices")
        if not isinstance(it.get("answer"), int) or not 0 <= it["answer"] < len(ch):
            errs.append(f"{where}: answer index out of range")
        if not it.get("text") or any(ord(c) > 0x7E and c not in "’‘“”—–" for c in it.get("text", "")):
            errs.append(f"{where}: text must be plain English")
    return errs


class SentenceBank:
    def __init__(self) -> None:
        data = json.loads((config.CONTENT_DIR / "sentences.json").read_text())
        errs = validate(data)
        if errs:
            raise ValueError("sentences.json is invalid: " + "; ".join(errs[:5]))
        self.items: dict[str, Sentence] = {
            it["id"]: Sentence(it["id"], it["level"], it["focus"], it["voice"], it["text"], it["q"], tuple(it["choices"]),
                               it["answer"], it.get("explain", "")) for it in data["items"]}
        self.manifest = load_manifest()

    def audio(self, s: Sentence) -> str | None:
        m = self.manifest.get(s.id) or {}
        return m.get("audio") if m.get("hash") == s.audio_key else None

    def by_level(self, level: int) -> list[Sentence]:
        return [s for s in self.items.values() if s.level == level]

    def missing_audio(self) -> list[str]:
        return [s.id for s in self.items.values() if not self.audio(s) or not (config.AUDIO_DIR / self.audio(s)).exists()]


_bank: SentenceBank | None = None


def bank() -> SentenceBank:
    global _bank
    if _bank is None:
        _bank = SentenceBank()
    return _bank


def reload_bank() -> SentenceBank:
    global _bank
    _bank = SentenceBank()
    return _bank


def manifest_path() -> Path:
    return config.AUDIO_DIR / "sentence_manifest.json"


def load_manifest() -> dict:
    p = manifest_path()
    return json.loads(p.read_text()) if p.exists() else {}


def build_audio(force: bool = False, workers: int = 3, log=print) -> dict:
    from . import tts
    config.ensure_dirs()
    b = SentenceBank()
    manifest = load_manifest()
    todo = [s for s in b.items.values()
            if force or (manifest.get(s.id) or {}).get("hash") != s.audio_key
            or not (config.AUDIO_DIR / (manifest.get(s.id) or {}).get("audio", "missing")).exists()]
    (config.AUDIO_DIR / "sentences").mkdir(parents=True, exist_ok=True)

    def one(s: Sentence):
        rel = f"sentences/{s.audio_key}.mp3"
        tts._say_to_mp3(s.text, s.voice, config.LEVEL_RATE.get(s.level, 175), config.AUDIO_DIR / rel)
        return s.id, {"audio": rel, "hash": s.audio_key}

    if todo:
        log(f"  sentences: {len(todo)} to synthesize")
        with ThreadPoolExecutor(workers) as ex:
            for sid, entry in ex.map(one, todo):
                manifest[sid] = entry
        manifest_path().write_text(json.dumps(manifest, indent=1))
    reload_bank()
    return manifest


def item(s: Sentence, rng: random.Random | None = None, reason: str | None = None) -> dict:
    """Session payload. The choices are shuffled; the secret keeps the right index."""
    rng = rng or random.Random()
    order = list(range(4))
    rng.shuffle(order)
    choices = [s.choices[i] for i in order]
    return {
        "type": "sentence",
        "public": {"type": "sentence", "audio": bank().audio(s), "level": s.level, "q": s.q, "choices": choices,
                   "focus": FOCUS_LABEL[s.focus], "reason": reason},
        "secret": {"sentence_id": s.id, "answer": order.index(s.answer)},
    }
