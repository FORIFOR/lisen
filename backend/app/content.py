"""Teaching material: clips (audio scripts with exact transcripts), segments, vocab.

Every clip's audio is synthesized from its script (see tts.py), so the transcript is
ground truth and each segment's audio maps 1:1 to a line of the script.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import config
from .scoring import tokenize


@dataclass
class Segment:
    id: str
    clip_id: str
    index: int
    speaker: str
    voice: str
    text: str
    level: int
    start: float = 0.0
    end: float = 0.0
    audio: str | None = None  # relative audio path for this line
    skill_tokens: dict[str, int] = field(default_factory=dict)


@dataclass
class Clip:
    id: str
    level: int
    pool: str
    kind: str
    title: str
    voices: dict[str, str]
    segments: list[Segment]
    questions: list[dict]
    vocab: list[dict]
    source: str = "builtin"
    audio: str | None = None
    duration: float = 0.0
    targets: list[str] = field(default_factory=list)   # vocabulary words a generated lesson trains
    topic: str = "daily"                                # see TOPIC_LABEL

    @property
    def speakers(self) -> int:
        return len({s.speaker for s in self.segments})

    def public(self, with_text: bool = True) -> dict:
        return {
            "id": self.id, "level": self.level, "kind": self.kind, "title": self.title,
            "audio": self.audio, "duration": self.duration, "speakers": self.speakers,
            "source": self.source, "topic": self.topic,
            "segments": [
                {"id": s.id, "index": s.index, "speaker": s.speaker, "start": s.start, "end": s.end,
                 "audio": s.audio, **({"text": s.text} if with_text else {})}
                for s in self.segments
            ],
        }


def _skill_profile(text: str) -> dict[str, int]:
    from .scoring import classify
    toks = tokenize(text)
    prof: dict[str, int] = {}
    for i, t in enumerate(toks):
        c = classify(t, toks[i - 1] if i else None)
        prof[c] = prof.get(c, 0) + 1
    return prof


def _build_clip(raw: dict, source: str, manifest: dict) -> Clip:
    cid = raw["id"]
    segs = []
    meta = manifest.get(cid, {})
    for i, (spk, text) in enumerate(raw["lines"]):
        m = (meta.get("segments") or [{}] * len(raw["lines"]))
        m = m[i] if i < len(m) else {}
        segs.append(Segment(
            id=f"{cid}#{i}", clip_id=cid, index=i, speaker=spk,
            voice=raw.get("voices", {}).get(spk, config.DEFAULT_VOICE), text=text, level=raw["level"],
            start=m.get("start", 0.0), end=m.get("end", 0.0), audio=m.get("audio"),
            skill_tokens=_skill_profile(text),
        ))
    vocab = []
    for j, (word, ja) in enumerate(raw.get("vocab", [])):
        seg = next((s for s in segs if re.search(rf"\b{re.escape(word)}\b", s.text, re.I)), None)
        vocab.append({"id": f"{cid}~{j}", "word": word, "ja": ja, "clip_id": cid,
                      "segment_id": seg.id if seg else None, "level": raw["level"]})
    return Clip(
        id=cid, level=raw["level"], pool=raw.get("pool", "train"), kind=raw.get("kind", "monologue"),
        title=raw.get("title", cid), voices=raw.get("voices", {}), segments=segs,
        questions=raw.get("questions", []), vocab=vocab, source=source,
        audio=meta.get("audio"), duration=meta.get("duration", 0.0), targets=list(raw.get("target_words", [])),
        topic=raw.get("topic") or _topic_map().get(cid) or ("imported" if raw.get("source_kind") == "notebooklm" else "daily"),
    )


_TOPICS: dict[str, str] | None = None
TOPIC_LABEL = {"daily": "日常", "business": "ビジネス", "tech": "AI・テクノロジー", "science": "科学・教養",
               "travel": "旅行", "conversation": "自然な会話", "imported": "取り込んだ音声"}


def _topic_map() -> dict[str, str]:
    global _TOPICS
    if _TOPICS is None:
        p = config.CONTENT_DIR / "topics.json"
        data = json.loads(p.read_text())["topics"] if p.exists() else {}
        _TOPICS = {}
        for topic, ids in data.items():
            for cid in ids:
                _TOPICS.setdefault(cid, topic)
    return _TOPICS


def _load_manifest() -> dict:
    p = config.AUDIO_DIR / "manifest.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def raw_clips() -> list[tuple[dict, str]]:
    out = []
    for p in sorted(config.CONTENT_DIR.glob("*.json")):
        data = json.loads(p.read_text())
        if isinstance(data, list):          # clip files; sentences.json / topics.json are not clips
            for raw in data:
                out.append((raw, "builtin"))
    if config.USER_CONTENT_DIR.exists():
        for p in sorted(config.USER_CONTENT_DIR.glob("*.json")):
            out.append((json.loads(p.read_text()), "user"))
    lessons = config.DATA_DIR / "lessons"   # generated vocabulary lessons (see lessons.py)
    if lessons.exists():
        for p in sorted(lessons.glob("*.json")):
            out.append((json.loads(p.read_text()), "lesson"))
    return out


class Library:
    def __init__(self) -> None:
        manifest = _load_manifest()
        self.clips: dict[str, Clip] = {}
        for raw, source in raw_clips():
            clip = _build_clip(raw, source, manifest)
            self.clips[clip.id] = clip
        self.segments: dict[str, Segment] = {s.id: s for c in self.clips.values() for s in c.segments}
        self.vocab: dict[str, dict] = {v["id"]: v for c in self.clips.values() for v in c.vocab}

    def train_clips(self) -> list[Clip]:
        return [c for c in self.clips.values() if c.pool == "train" and c.audio]

    def test_clips(self, form: str) -> list[Clip]:
        return sorted([c for c in self.clips.values() if c.pool == f"test-{form}"], key=lambda c: c.level)

    def missing_audio(self) -> list[str]:
        return [c.id for c in self.clips.values() if not c.audio]


_lib: Library | None = None


def library() -> Library:
    global _lib
    if _lib is None:
        _lib = Library()
    return _lib


def reload_library() -> Library:
    global _lib
    _lib = Library()
    from . import study
    study.reload_bank()      # notes and transfer sentences follow the clips (generated lessons carry their own)
    return _lib
