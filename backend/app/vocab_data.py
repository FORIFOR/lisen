"""TOEIC-style business vocabulary: the word database, its validation, and word audio lookup.

Only values that the dataset itself provides are used. `tier` is the ListenFlow authors' own
difficulty band (1-4); CEFR level / TOEIC relevance stay null until a licensed source provides them.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import config

DATASET_PATH = Path(__file__).resolve().parent.parent / "content" / "vocab" / "toeic_core.json"
POS = {"n", "v", "adj", "adv"}
TIERS = (1, 2, 3, 4)
_JA = re.compile(r"[぀-ヿ一-鿿]")


@dataclass(frozen=True)
class Word:
    id: str
    lemma: str
    pos: str
    meaning: str
    tier: int
    topic: str
    group: str | None
    example: str
    form: str          # the inflected form used in the example sentence
    cefr_level: str | None = None
    toeic_relevance: str | None = None
    pronunciation: str | None = None

    @property
    def audio_key(self) -> str:
        # file names must not reveal the word (the audio question would be answered by the URL)
        return hashlib.sha1(f"lf-word:{self.id}".encode()).hexdigest()[:12]

    def forms(self) -> set[str]:
        """Surface forms that count as this word appearing in a text."""
        base = self.lemma.lower()
        out = {base, self.form.lower(), base + "s", base + "es", base + "ed", base + "d", base + "ing"}
        if base.endswith("e"):
            out.add(base[:-1] + "ing")
        if base.endswith("y"):
            out |= {base[:-1] + "ies", base[:-1] + "ied"}
        return out

    def appears_in(self, text: str) -> str | None:
        """Return the matching surface form if this word occurs in `text` (word boundaries, any case)."""
        for f in sorted(self.forms(), key=len, reverse=True):
            m = re.search(rf"\b{re.escape(f)}\b", text, re.I)
            if m:
                return m.group(0)
        return None


def _meaning_parts(meaning: str) -> set[str]:
    """2-character chunks of a Japanese gloss; overlapping chunks mean the glosses may be synonyms."""
    s = re.sub(r"[、。・（）()\s]", "", meaning)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def overlaps(a: Word, b: Word) -> bool:
    if a.id == b.id:
        return True
    if a.group and a.group == b.group:
        return True
    if a.meaning == b.meaning:
        return True
    return bool(_meaning_parts(a.meaning) & _meaning_parts(b.meaning))


def validate_dataset(data: dict) -> list[str]:
    """Return a list of problems (empty = valid). Checks duplicates, malformed entries and license."""
    problems: list[str] = []
    meta = data.get("dataset") or {}
    if not meta.get("license") or not meta.get("source"):
        problems.append("dataset: license and source are required")
    seen_ids: set[str] = set()
    seen_lemmas: set[str] = set()
    seen_examples: set[str] = set()
    for i, w in enumerate(data.get("words") or []):
        where = f"words[{i}] {w.get('lemma')!r}"
        for key in ("id", "lemma", "pos", "meaning", "tier", "topic", "examples"):
            if not w.get(key):
                problems.append(f"{where}: missing {key}")
        if problems and problems[-1].startswith(where):
            continue
        if w["id"] in seen_ids:
            problems.append(f"{where}: duplicate id")
        if w["lemma"].lower() in seen_lemmas:
            problems.append(f"{where}: duplicate lemma")
        seen_ids.add(w["id"])
        seen_lemmas.add(w["lemma"].lower())
        if not re.fullmatch(r"[a-z][a-z\-]*", w["lemma"]):
            problems.append(f"{where}: lemma must be lower-case letters")
        if w["pos"] not in POS:
            problems.append(f"{where}: unknown pos {w['pos']}")
        if w["tier"] not in TIERS:
            problems.append(f"{where}: tier must be 1-4")
        if not _JA.search(w["meaning"]):
            problems.append(f"{where}: meaning must be Japanese")
        if w.get("cefr_level") is not None and w["cefr_level"] not in {"A1", "A2", "B1", "B2", "C1", "C2"}:
            problems.append(f"{where}: invalid cefr_level")
        for ex in w["examples"]:
            en, form = ex.get("en", ""), ex.get("form", "")
            if not en or not form:
                problems.append(f"{where}: example needs en and form")
                continue
            if not re.search(rf"\b{re.escape(form)}\b", en, re.I):
                problems.append(f"{where}: example does not contain {form!r}")
            if not form.lower().startswith(w["lemma"][:max(3, len(w["lemma"]) - 2)]):
                problems.append(f"{where}: form {form!r} is not an inflection of the lemma")
            if _JA.search(en):
                problems.append(f"{where}: example must be English")
            if not 3 <= len(en.split()) <= 25:
                problems.append(f"{where}: example length out of range")
            if en in seen_examples:
                problems.append(f"{where}: duplicate example")
            seen_examples.add(en)
    if len(seen_ids) < 40:
        problems.append("dataset: too few words for a diagnostic")
    return problems


class VocabLibrary:
    def __init__(self, path: Path = DATASET_PATH) -> None:
        self.raw = json.loads(path.read_text())
        problems = validate_dataset(self.raw)
        if problems:
            raise ValueError("vocabulary dataset is invalid:\n" + "\n".join(problems[:20]))
        self.meta = self.raw["dataset"]
        self.words: dict[str, Word] = {}
        for w in self.raw["words"]:
            ex = w["examples"][0]
            self.words[w["id"]] = Word(
                id=w["id"], lemma=w["lemma"], pos=w["pos"], meaning=w["meaning"], tier=w["tier"],
                topic=w["topic"], group=w.get("group"), example=ex["en"], form=ex["form"],
                cefr_level=w.get("cefr_level"), toeic_relevance=w.get("toeic_relevance"),
                pronunciation=w.get("pronunciation"))
        self.manifest = load_audio_manifest()

    def by_tier(self, tier: int) -> list[Word]:
        return [w for w in self.words.values() if w.tier == tier]

    def audio(self, word: Word) -> dict:
        """{'word': rel path, 'example': rel path} for words whose audio exists."""
        m = self.manifest.get(word.id) or {}
        out = {}
        for k in ("word", "example"):
            rel = m.get(k)
            if rel and (config.AUDIO_DIR / rel).exists():
                out[k] = rel
        return out

    def missing_audio(self) -> list[str]:
        return [w.id for w in self.words.values() if len(self.audio(w)) < 2]


def manifest_path() -> Path:
    return config.AUDIO_DIR / "vocab_manifest.json"


def load_audio_manifest() -> dict:
    p = manifest_path()
    return json.loads(p.read_text()) if p.exists() else {}


_lib: VocabLibrary | None = None


def vocab_library() -> VocabLibrary:
    global _lib
    if _lib is None:
        _lib = VocabLibrary()
    return _lib


def reload_vocab_library() -> VocabLibrary:
    global _lib
    _lib = VocabLibrary()
    return _lib
