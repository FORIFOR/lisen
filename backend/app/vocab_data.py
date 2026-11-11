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

VOCAB_DIR = Path(__file__).resolve().parent.parent / "content" / "vocab"
DATASET_PATH = VOCAB_DIR / "toeic_core.json"
# toeic_core: original CC0 set · ngsl_tsl: headwords from NGSL 1.2 / TSL 1.2 (CC BY-SA 4.0), see content/vocab/sources
DATASET_PATHS = (DATASET_PATH, VOCAB_DIR / "ngsl_tsl.json")
POS = {"n", "v", "adj", "adv", "prep", "conj", "pron"}
# audio a person has to listen to before it is used (e.g. speech recognition heard "clock" for "clerk")
AUDIO_REVIEW_PATH = VOCAB_DIR / "audio_review.json"
AUDIO_KINDS = ("word", "example", "transfer")
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
    scene: str | None = None          # where the word is typically used (Japanese)
    example_ja: str | None = None     # translation of the study example
    transfer: str | None = None       # a second, unseen sentence: same sense, different scene and speaker
    transfer_form: str | None = None
    transfer_ja: str | None = None
    source: str | None = None         # "tsl" / "ngsl" headword lists, None for the original set
    rank: int | None = None
    dataset: str = "toeic_core"

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


def _tokens(s: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z']+", s)}


def sense_matches(meaning: str, ja: str) -> bool:
    """Does a Japanese translation carry the gloss? (shares a 2-character chunk, or contains one gloss)"""
    if _meaning_parts(meaning) & _meaning_parts(ja):
        return True
    for g in (g.strip() for g in re.split(r"[、,]", meaning)):
        if not g:
            continue
        if g in ja:
            return True
        # inflected verbs / adjectives: 乗る -> 乗って, 結ぶ -> 結び (the kanji stem without its okurigana)
        stem = re.sub(r"[ぁ-ん]+$", "", g)
        if stem and stem != g and re.fullmatch(r"[一-鿿々]+", stem) and stem in ja:
            return True
    return False


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
        roles = [ex.get("role") for ex in w["examples"]]
        if any(roles) and roles[:2] != ["study", "transfer"]:
            problems.append(f"{where}: examples must be [study, transfer]")
        if w.get("source") is not None and w["source"] not in ("tsl", "ngsl"):
            problems.append(f"{where}: unknown source {w['source']}")
        for ex in w["examples"]:
            en, form = ex.get("en", ""), ex.get("form", "")
            if ex.get("role") and not ex.get("ja"):
                problems.append(f"{where}: {ex['role']} example needs a Japanese translation")
            if ex.get("ja") and not sense_matches(w["meaning"], ex["ja"]):
                # the translation must carry the gloss: otherwise the sentence uses another sense of the word
                problems.append(f"{where}: example translation does not use the meaning {w['meaning']!r} (sense mismatch)")
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
        if len(w["examples"]) >= 2 and w["examples"][1].get("role") == "transfer":
            a, b = (_tokens(x["en"]) for x in w["examples"][:2])
            if len(a & b) / max(1, len(a | b)) > 0.4:
                problems.append(f"{where}: transfer sentence is too close to the study sentence")
    if len(seen_ids) < 40:
        problems.append("dataset: too few words for a diagnostic")
    return problems


def audit(words: list[Word]) -> list[str]:
    """Cross-dataset warnings a human should look at (not load errors): identical glosses on unrelated
    words, which would make one 4-choice question have two right answers if both were offered."""
    out = []
    by_meaning: dict[str, list[Word]] = {}
    for w in words:
        by_meaning.setdefault(w.meaning, []).append(w)
    for m, ws in by_meaning.items():
        if len(ws) > 1 and len({w.group for w in ws if w.group}) != 1:
            out.append(f"same meaning {m!r}: {', '.join(w.lemma for w in ws)}")
    return out


class VocabLibrary:
    def __init__(self, paths=None) -> None:
        if isinstance(paths, Path):
            paths = (paths,)
        paths = [p for p in (paths or DATASET_PATHS) if p.exists()]
        self.datasets: list[dict] = []
        self.words: dict[str, Word] = {}
        lemmas: dict[str, str] = {}
        for path in paths:
            raw = json.loads(path.read_text())
            problems = validate_dataset(raw)
            name = path.stem
            for w in raw.get("words") or []:
                other = lemmas.get(w.get("lemma", "").lower())
                if other and other != name:
                    problems.append(f"{w['lemma']!r}: also in {other}.json (duplicate across datasets)")
                lemmas.setdefault(w.get("lemma", "").lower(), name)
            if problems:
                raise ValueError(f"vocabulary dataset {path.name} is invalid:\n" + "\n".join(problems[:20]))
            self.datasets.append({**raw["dataset"], "file": path.name, "words": len(raw["words"])})
            for w in raw["words"]:
                ex = w["examples"][0]
                tr = w["examples"][1] if len(w["examples"]) > 1 and w["examples"][1].get("role") == "transfer" else {}
                self.words[w["id"]] = Word(
                    id=w["id"], lemma=w["lemma"], pos=w["pos"], meaning=w["meaning"], tier=w["tier"],
                    topic=w["topic"], group=w.get("group"), example=ex["en"], form=ex["form"],
                    cefr_level=w.get("cefr_level"), toeic_relevance=w.get("toeic_relevance"),
                    pronunciation=w.get("pronunciation"), scene=w.get("scene"), example_ja=ex.get("ja"),
                    transfer=tr.get("en"), transfer_form=tr.get("form"), transfer_ja=tr.get("ja"),
                    source=w.get("source"), rank=w.get("rank"), dataset=name)
        self.raw = {"dataset": self.datasets[0], "words": [w for p in paths for w in json.loads(p.read_text())["words"]]}
        self.meta = {"name": " + ".join(d["name"] for d in self.datasets),
                     "license": " / ".join(f"{d['file']}: {d['license']}" for d in self.datasets),
                     "source": self.datasets[0]["source"]}
        self.manifest = load_audio_manifest()
        self.review = load_audio_review()

    def held(self, word: Word, kind: str) -> bool:
        """On hold until someone has listened to it. An entry for an older wording no longer applies."""
        r = self.review.get(f"{word.id}:{kind}")
        return bool(r) and r.get("status") != "ok" and r.get("text") == spoken_text(word, kind)

    def held_items(self) -> list[str]:
        return [k for k in self.review if (w := self.words.get(k.split(":")[0])) and self.held(w, k.split(":")[1])]

    def by_tier(self, tier: int) -> list[Word]:
        return [w for w in self.words.values() if w.tier == tier]

    def audio(self, word: Word) -> dict:
        """{'word': rel path, 'example': rel path} for words whose audio exists."""
        return {k: v for k, v in self.files(word).items() if not self.held(word, k)}

    def files(self, word: Word) -> dict:
        """All generated files, including held ones (for checking them)."""
        m = self.manifest.get(word.id) or {}
        out = {}
        for k in AUDIO_KINDS:
            rel = m.get(k)
            if rel and (config.AUDIO_DIR / rel).exists():
                out[k] = rel
        return out

    def missing_audio(self) -> list[str]:
        """Words whose audio files were not generated (held audio exists; it is only not served)."""
        return [w.id for w in self.words.values() if len(self.files(w)) < (3 if w.transfer else 2)]


def load_audio_review() -> dict:
    """{"<word_id>:<kind>": {"text", "status": "hold" | "ok", "reason", "voice"?}}"""
    if not AUDIO_REVIEW_PATH.exists():
        return {}
    return json.loads(AUDIO_REVIEW_PATH.read_text()).get("items", {})


def spoken_text(word: Word, kind: str) -> str | None:
    return {"word": word.lemma, "example": word.example, "transfer": word.transfer}[kind]


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
