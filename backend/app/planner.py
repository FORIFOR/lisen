"""Session planning: turns "how many minutes today" into an ordered list of training blocks.

Longer sessions do not repeat the same items; they add *different kinds* of work
(new audio, weakness drills, multi-speaker, long-form, vocabulary) so extra time buys
extra learning rather than padding.
"""
from __future__ import annotations

BLOCK_INFO = {
    "review": ("復習・ウォームアップ", "FSRS で今日復習すべき音声と、聞き分けの準備運動"),
    "new": ("初見リスニング・理解", "まだ聞いたことのない音声を、まず文字なしで理解する"),
    "weak": ("弱点別の集中訓練", "あなたの聞き取りミスが多い音のパターンを集中的に"),
    "shadow": ("シャドーイング・総仕上げ", "今日の英文を音声に重ねて声に出す"),
    "multi": ("複数話者の会話", "話者が入れ替わる会話を追う"),
    "long": ("長めの実践リスニング", "長い講義・ニュースで要点と推測を捉える"),
    "vocab": ("語彙の聞き取り強化", "音声の中のキーワードを聞き取り、意味と結びつける"),
    "words": ("TOEIC 語彙", "忘れかけた単語を FSRS で復習し、文字・音声・文の中の3つで確認する"),
    "final": ("総合復習", "今日間違えた箇所をもう一度確認する"),
    "break": ("休憩", "目と耳を休めましょう"),
    "test": ("初見リスニングテスト", "一度も聞いていない音声で、現在の実力を測定"),
}

TEMPLATES: dict[int, list[tuple[str, int]]] = {
    15: [("review", 5), ("new", 10)],
    30: [("review", 5), ("new", 10), ("weak", 7), ("words", 5), ("shadow", 3)],
    60: [("review", 8), ("words", 6), ("new", 15), ("weak", 10), ("vocab", 6), ("new", 8), ("shadow", 7)],
    90: [("review", 10), ("new", 15), ("weak", 12), ("multi", 15), ("words", 8), ("vocab", 7), ("weak", 8),
         ("shadow", 8), ("final", 7)],
    120: [("review", 10), ("new", 15), ("weak", 15), ("multi", 15), ("long", 20), ("words", 10), ("vocab", 8),
          ("weak", 8), ("shadow", 9), ("final", 10)],
}
EXTRA_CYCLE: list[tuple[str, int]] = [("new", 10), ("weak", 10), ("words", 5), ("long", 10), ("multi", 10),
                                      ("vocab", 5), ("shadow", 5)]


def _block(kind: str, minutes: float) -> dict:
    label, desc = BLOCK_INFO[kind]
    return {"kind": kind, "minutes": round(minutes, 1), "label": label, "description": desc}


def extra_blocks(minutes: int, start: int = 0) -> list[dict]:
    out: list[dict] = []
    remaining = minutes
    i = start
    while remaining >= 5:
        kind, dur = EXTRA_CYCLE[i % len(EXTRA_CYCLE)]
        dur = min(dur, remaining)
        out.append(_block(kind, dur))
        remaining -= dur
        i += 1
    if remaining > 0 and out:
        out[-1]["minutes"] = round(out[-1]["minutes"] + remaining, 1)
    elif remaining > 0:
        out.append(_block("new", remaining))
    return out


def insert_breaks(blocks: list[dict], settings: dict) -> list[dict]:
    if not settings.get("break_enabled", True):
        return blocks
    every = max(20, int(settings.get("break_every", 45)))
    length = min(10, max(3, int(settings.get("break_minutes", 5))))
    total = sum(b["minutes"] for b in blocks)
    if total < 60:
        return blocks
    out: list[dict] = []
    since = 0.0
    for idx, b in enumerate(blocks):
        out.append(b)
        since += b["minutes"]
        if since >= every and idx < len(blocks) - 1:
            out.append(_block("break", length))
            since = 0.0
    return out


def build_plan(minutes: int, settings: dict | None = None, mode: str = "standard") -> list[dict]:
    settings = settings or {}
    minutes = int(minutes)
    if mode == "review":
        return [_block("review", max(5, minutes))]
    if minutes < 15:
        base = TEMPLATES[15]
        scale = minutes / 15
        blocks = [_block(k, max(2, m * scale)) for k, m in base]
    else:
        key = max(k for k in TEMPLATES if k <= minutes)
        blocks = [_block(k, m) for k, m in TEMPLATES[key]]
        rest = minutes - key
        if rest:
            extra = extra_blocks(rest)
            # keep the closing blocks (shadow / final) at the end
            tail = [b for b in blocks if b["kind"] in ("shadow", "final")]
            head = [b for b in blocks if b["kind"] not in ("shadow", "final")]
            blocks = head + extra + tail
    return insert_breaks(blocks, settings)


def study_minutes(blocks: list[dict]) -> float:
    return round(sum(b["minutes"] for b in blocks if b["kind"] != "break"), 1)


def summarize(blocks: list[dict]) -> dict:
    return {
        "blocks": blocks,
        "study_minutes": study_minutes(blocks),
        "break_minutes": round(sum(b["minutes"] for b in blocks if b["kind"] == "break"), 1),
    }
