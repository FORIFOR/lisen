"""Daily English News: today's stories as listening lessons at three levels (A2 / B1 / B2).

    collect   RSS headlines and summaries -> news_sources            (news_feeds; no Gemini)
    select    a few fresh stories in the learner's topics, one per event, those covered by several outlets first
    generate  Gemini retells ONE source in its own words at each level, with Japanese translations, 3 comprehension
              questions per level and 5-8 useful words (each with an unseen sentence in another situation)
    verify    in code, not by asking Gemini again: every number, name, date word and quotation must be in the source,
              no long word sequence copied from it, numbers kept in the translation, questions well formed, the
              word entries pass the word-book checks
    audio     macOS `say`, cached by (text, voice, rate, engine) hash; every sentence is checked with local speech
              recognition, and a level whose audio does not match its text is not offered
    publish   only lessons that passed every check; the rest are kept with the reason (for the status page)

A run is idempotent: a story becomes at most one lesson, a day gets at most `news_per_day` lessons, and audio that
already matches its text is never synthesized again. The morning run starts at `news_hour` (local time) when the
server is running; if the Mac was asleep, it runs when the server next checks (every 10 minutes).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import random
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import asr, config, db, lessons, tts
from . import news_feeds as F
from .vocab_data import POS, sense_matches, validate_dataset, vocab_library

log = logging.getLogger(__name__)

GENERATOR_VERSION = 1
LEVELS = ("A2", "B1", "B2")
LEVEL_LABEL = {"A2": "初級", "B1": "中級", "B2": "上級"}
LEVEL_TIER = {"A2": 2, "B1": 3, "B2": 4}                 # word-book tier of words added from this level
LEVEL_RATE = {"A2": 155, "B1": 170, "B2": 185}            # words per minute (`say -r`)
SENTENCES = {"A2": (5, 8), "B1": (6, 10), "B2": (7, 12)}
MAX_WORDS = {"A2": 16, "B1": 22, "B2": 25}
QUESTIONS = 3
VOCAB = (5, 8)
TRANSFER_CHECKS = 3                                       # unseen sentences asked at the end of a lesson
MIN_SUMMARY_WORDS = 40
MAX_COPY_NGRAM = 8                                        # longest word sequence that may match the source
VOICES = tts.VOCAB_EXAMPLE_VOICES
GEMINI_TIMEOUT = 150.0
# thinking tokens count toward maxOutputTokens and are billed as output; LOW thinking (Gemini 3+) keeps the answer
# well under the limit, and a JSON Schema (responseJsonSchema) makes the answer syntactically valid JSON
MAX_OUTPUT_TOKENS = 12000
THINKING_LEVEL = "LOW"
# USD per 1M tokens (input, output incl. thinking), standard paid tier; unknown models use the dearest Flash price
PRICES = {"gemini-3.8-flash": (0.75, 3.75), "gemini-3.6-flash": (0.75, 3.75), "gemini-3-flash-preview": (0.50, 3.00),
          "gemini-3.5-flash-lite": (0.30, 2.50), "gemini-2.5-flash": (0.30, 2.50)}
PRICE_UNKNOWN = (1.50, 9.00)
MAX_CALLS_PER_STORY = 2        # Gemini calls ever made for one story (billing can never repeat beyond this)
MAX_TRIES_PER_STORY = 3        # runs that may try one story (API errors, audio)
RETRY_AFTER = dt.timedelta(hours=1)
# failure kinds. FIXABLE: one regeneration with the problems listed. Later runs retry only RETRYABLE ones.
FIXABLE = {"fact", "copy", "translation", "question", "format", "vocab", "json"}
RETRYABLE = {"api", "audio"}
RUN_EVERY_S = 600
MAX_FAILED_RUNS = 3

_JA = re.compile(r"[぀-ヿ一-鿿]")
_run_lock = threading.Lock()
_started = False


class NewsError(Exception):
    def __init__(self, status: int, message: str, usage: dict | None = None, kind: str = "api", raw: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.usage = usage or {}         # tokens of a call that was billed but could not be used
        self.kind = kind
        self.raw = raw or (usage or {}).get("raw")


# --- 1. collect ----------------------------------------------------------------------------------

def collect(opener=None, feeds=F.FEEDS) -> dict:
    found, errors = [], []
    for f in feeds:
        try:
            # only recent stories are kept (a feed may carry its whole history: OpenAI's has over 1,000 items)
            found += [(e, f) for e in F.fetch_feed(f, opener) if F.fresh(e)]
        except F.FeedError as e:
            errors.append(str(e))
    new = 0
    now = db.now_iso()
    with db.tx() as conn:
        for e, f in found:
            ok = f.lesson and (len(e.summary.split()) >= MIN_SUMMARY_WORDS or len(e.content.split()) >= MIN_SUMMARY_WORDS)
            cur = conn.execute(
                "INSERT OR IGNORE INTO news_sources (id, feed, publisher, category, kind, url, title, summary, content,"
                " published_at, fetched_at, lesson_ok) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (e.id, f.id, f.publisher, f.category, f.kind, e.url, e.title, e.summary[:4000], e.content[:20000],
                 e.published, now, int(ok)))
            new += cur.rowcount
    return {"found": len(found), "new": new, "errors": errors}


# --- 2. select -----------------------------------------------------------------------------------

def select(conn, topics: list[str], count: int, now: dt.datetime | None = None) -> list[dict]:
    """Fresh stories in the chosen topics, at most one per event; those reported by another outlet first,
    then the most recent. Stories already turned into a lesson (or rejected) are not picked again."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=F.MAX_AGE_DAYS)).isoformat(timespec="seconds")
    rows = [dict(r) for r in conn.execute("SELECT * FROM news_sources WHERE published_at>=? ORDER BY published_at DESC", (since,))]
    # a story is used once it has a lesson or a permanent failure; a retryable failure comes back after a while
    used = {r["source_id"] for r in conn.execute(
        "SELECT source_id FROM news_lessons WHERE NOT ("
        " (status='retry' AND COALESCE(retry_after,'')<=? AND tries<? AND (failure='audio' OR calls<?))"
        " OR (status='draft' AND updated_at<=? AND tries<? AND calls<?))",      # a run that was cut off (server stopped)
        (now.isoformat(timespec="seconds"), MAX_TRIES_PER_STORY, MAX_CALLS_PER_STORY,
         (now - RETRY_AFTER).isoformat(timespec="seconds"), MAX_TRIES_PER_STORY, MAX_CALLS_PER_STORY))}
    recent = [json.loads(r["data"])["source"]["title"] for r in conn.execute(
        "SELECT data FROM news_lessons WHERE status='published' AND date>=?", ((now - dt.timedelta(days=3)).date().isoformat(),))]
    scored = []
    for r in rows:
        if not r["lesson_ok"] or r["category"] not in topics or r["id"] in used:
            continue
        if any(F.same_story(r["title"], t) for t in recent):
            continue
        corr = [{"publisher": o["publisher"], "title": o["title"], "url": o["url"], "kind": o["kind"]}
                for o in rows if o["publisher"] != r["publisher"] and F.same_story(r["title"], o["title"])]
        age_h = (now - dt.datetime.fromisoformat(r["published_at"])).total_seconds() / 3600
        scored.append((min(len({c["publisher"] for c in corr}), 2) * 24 - age_h, r, corr))
    scored.sort(key=lambda x: -x[0])
    picked: list[dict] = []
    by_topic = {t: [s for s in scored if s[1]["category"] == t] for t in topics}
    while len(picked) < count and any(by_topic.values()):
        for t in topics:                      # one story per topic in turn
            while by_topic[t]:
                _, r, corr = by_topic[t].pop(0)
                if any(F.same_story(r["title"], p["title"]) for p in picked):
                    continue
                picked.append({**r, "corroborated": corr})
                break
            if len(picked) >= count:
                break
    return picked


# --- 3. generate (Gemini) --------------------------------------------------------------------------

def source_text(src: dict) -> str:
    return "\n".join(x for x in (src["title"], src["summary"], src.get("content") or "") if x)


def vocab_level(levels) -> str:
    return "B1" if "B1" in levels else list(levels)[len(levels) // 2]


def prompt(src: dict, problems: list[str] | None = None, levels=LEVELS) -> str:
    when = src.get("published_at") or "unknown"
    text = (src.get("content") or "")[:6000] or src["summary"]
    vl = vocab_level(levels)
    style = {"A2": "short sentences, common words, simple tenses", "B1": "clear sentences", "B2": "natural news English"}
    versions = "\n".join(f"   {lv}: {SENTENCES[lv][0]}-{SENTENCES[lv][1]} sentences, at most {MAX_WORDS[lv]} words each, {style[lv]}."
                         for lv in levels)
    fix = ""
    if problems:
        fix = ("\n\nYour previous answer was rejected by an automatic check for these reasons. Fix every one of them:\n- "
               + "\n- ".join(problems[:12]))
    return f"""You write English listening lessons for a Japanese adult learner from ONE news source.

SOURCE (the only facts you may use)
Publisher: {src['publisher']} ({F.KIND_LABEL.get(src['kind'], src['kind'])})
Published: {when}
Headline: {src['title']}
Text: {text}

RULES
1. Use only facts stated in the SOURCE. Do not add background knowledge, causes, reactions, dates, days of the week,
   numbers, names, places or quotations that are not in the SOURCE.
2. Keep every attribution: if the source says something was reported, alleged, claimed or expected, say so. Never
   state an allegation as a fact. Keep who said what: the reporter's own descriptions and judgements (e.g. "could
   lay claim to being the best") are not said by the people in the story; do not put them in a person's or a
   company's mouth. Do not add causes, conclusions or predictions (e.g. "consequently", "this might change ...")
   that the source does not state.
3. Retell in your own words. Never copy more than 6 words in a row from the SOURCE. Use no quotation marks.
4. Write numbers with digits exactly as in the SOURCE (e.g. 10%, £10,000, 2026). Keep names spelled as in the SOURCE.
5. Write {"one version" if len(levels) == 1 else f"{len(levels)} self-contained versions"} of the story ("levels"):
{versions}
   Each sentence has a natural Japanese translation ("ja") that keeps every number and name.
6. For each level, write exactly {QUESTIONS} comprehension questions in Japanese about that level's text. Each has 4
   Japanese choices; exactly one is supported by the text, the other three are contradicted by it or not stated in it.
   No "all / none of the above" choices. "evidence": the indexes (0-based) of the sentences that support the answer.
   "explanation" (Japanese) says why.
7. "vocabulary": {VOCAB[0]}-{VOCAB[1]} useful English words from the {vl} text that a learner preparing for TOEIC should
   know (not names, not numbers). lemma: lower-case dictionary form. pos: one of n, v, adj, adv, prep, conj, pron.
   meaning: a short Japanese gloss of the sense used here. form: the word exactly as it appears in {vl} sentence
   "sentence" (0-based index); the Japanese translation of that sentence must contain the gloss. "transfer": a NEW
   sentence (8-18 words) using the same word in the same sense in a different everyday or workplace situation
   unrelated to this story, with no names, plus its Japanese translation (which must contain the gloss) and the
   exact form used.
8. If the SOURCE does not contain enough facts for a lesson, set "status" to "insufficient_evidence" and leave the
   other fields empty.{fix}"""


def response_schema(levels=LEVELS) -> dict:
    """JSON Schema for responseJsonSchema: the answer is valid JSON of this shape (values are still checked in code)."""
    s = {"type": "string"}
    sentence = {"type": "object", "properties": {"en": s, "ja": s}, "required": ["en", "ja"]}
    question = {"type": "object", "properties": {
        "q": s, "choices": {"type": "array", "items": s, "minItems": 4, "maxItems": 4},
        "answer": {"type": "integer", "minimum": 0, "maximum": 3},
        "evidence": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1}, "explanation": s},
        "required": ["q", "choices", "answer", "evidence", "explanation"]}
    level = {lv: {"type": "object", "properties": {
        "sentences": {"type": "array", "items": sentence, "minItems": SENTENCES[lv][0], "maxItems": SENTENCES[lv][1]},
        "questions": {"type": "array", "items": question, "minItems": QUESTIONS, "maxItems": QUESTIONS}},
        "required": ["sentences", "questions"]} for lv in levels}
    word = {"type": "object", "properties": {
        "lemma": s, "pos": {"type": "string", "enum": sorted(POS)}, "meaning": s, "form": s,
        "sentence": {"type": "integer", "minimum": 0},
        "transfer": {"type": "object", "properties": {"en": s, "form": s, "ja": s}, "required": ["en", "form", "ja"]}},
        "required": ["lemma", "pos", "meaning", "form", "sentence", "transfer"]}
    return {"type": "object", "properties": {
        "status": {"type": "string", "enum": ["ok", "insufficient_evidence"]},
        "title_en": s, "title_ja": s, "summary_ja": s,
        "levels": {"type": "object", "properties": level, "required": list(levels)},
        "vocabulary": {"type": "array", "items": word, "maxItems": VOCAB[1]}},
        "required": ["status", "title_en", "title_ja", "summary_ja", "levels", "vocabulary"]}


def price(model: str | None) -> tuple[float, float]:
    m = (model or "").removeprefix("models/")
    return next((p for k, p in PRICES.items() if m == k or m.startswith(k + "-")), PRICE_UNKNOWN)


def cost(usage: dict, model: str | None = None) -> float:
    pin, pout = price(model or usage.get("model"))
    return (usage.get("prompt", 0) * pin + usage.get("output", 0) * pout) / 1e6


def worst_case_cost(text: str, model: str | None) -> float:
    """The most one call could cost: the prompt (~3 characters per token, generously) plus the output limit."""
    pin, pout = price(model)
    return (len(text) / 2.5 * pin + MAX_OUTPUT_TOKENS * pout) / 1e6


def generation_config(levels=LEVELS) -> dict:
    cfg = {"responseMimeType": "application/json", "responseJsonSchema": response_schema(levels),
           "temperature": 0.3, "maxOutputTokens": MAX_OUTPUT_TOKENS}
    if re.match(r"gemini-([3-9]|\d\d)|gemini-flash-latest|gemini-pro-latest", lessons.gemini_model()):
        cfg["thinkingConfig"] = {"thinkingLevel": THINKING_LEVEL}    # Gemini 3+ only (an error on earlier models)
    return cfg


def call_gemini(text: str, levels=LEVELS) -> tuple[dict, dict]:
    """One generateContent call. Returns (parsed JSON, usage incl. model, finish reason and the raw response).
    Raises NewsError with `kind`: api (network / 5xx / 429: retry later), auth (key / model), max_tokens, blocked
    (SAFETY, RECITATION ...), json (not valid JSON); `usage` and `raw` are kept for billed calls."""
    key = lessons.gemini_key()
    if not key:
        raise NewsError(503, "Gemini の API キーが設定されていません", kind="auth")
    cfg = generation_config(levels)
    body = {"contents": [{"parts": [{"text": text}]}], "generationConfig": cfg}
    req = urllib.request.Request(f"{lessons.GEMINI_API}/{lessons.gemini_model()}:generateContent",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    request = {"model": lessons.gemini_model(), "generationConfig": {k: v for k, v in cfg.items() if k != "responseJsonSchema"}}
    try:
        with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        kind = "auth" if e.code in (400, 401, 403, 404) else "api"
        raise NewsError(e.code, lessons._gemini_error(e).msg, kind=kind, raw={"request": request, "http_status": e.code}) from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        raise NewsError(502, lessons._gemini_error(e).msg, kind="api", raw={"request": request, "error": type(e).__name__}) from e
    meta = data.get("usageMetadata") or {}
    cand = (data.get("candidates") or [{}])[0] or {}
    finish = cand.get("finishReason")
    usage = {"prompt": int(meta.get("promptTokenCount") or 0),
             "output": int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0),
             "thoughts": int(meta.get("thoughtsTokenCount") or 0), "model": data.get("modelVersion"), "finish": finish,
             "raw": {"request": request, "response": data}}
    if finish == "MAX_TOKENS":
        raise NewsError(502, f"Gemini の出力が上限（{MAX_OUTPUT_TOKENS}トークン）で途中で切れました", usage=usage, kind="max_tokens")
    if finish and finish not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        raise NewsError(502, f"Gemini が出力を止めました（{finish}）", usage=usage, kind="blocked")
    try:
        out = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
        return json.loads(out), usage
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise NewsError(502, f"Gemini の応答が JSON として読めませんでした（終了理由 {finish or '不明'}）", usage=usage, kind="json") from e


# --- 4. verify (in code) ---------------------------------------------------------------------------

NUMBER_WORDS = {w: float(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
NUMBER_WORDS.update({"thirty": 30.0, "forty": 40.0, "fifty": 50.0, "dozen": 12.0})
MULTIPLIERS = {"hundred": 1e2, "thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6, "billion": 1e9,
               "bn": 1e9, "trillion": 1e12, "tn": 1e12}
JA_UNITS = {"千": 1e3, "万": 1e4, "億": 1e8, "兆": 1e12}
_FW = str.maketrans("０１２３４５６７８９，．", "0123456789,.")
DATE_WORDS = set("monday tuesday wednesday thursday friday saturday sunday january february march april june july "
                 "august september october november december yesterday tomorrow".split())
FUNCTION_WORDS = set("a an the this that these those it its he she they we you i his her their our there here "
                     "in on at for from by with after before when while as but and or so if because although now "
                     "today tonight one some many most several all both each every no not more less other another "
                     "what who which where why how meanwhile however also still yet then later earlier".split())
ALWAYS_OK = {"I", "AI", "OK", "TV"}
# a choice that is only "all / none of the above" (not a sentence that happens to contain すべて)
META_CHOICE = re.compile(r"^\s*((上記|以上)の?)?(すべて|全て|いずれ|どれ)(も|が)?(正しい|当てはまる|当てはまらない|違う|でもない|ではない)?[。.]?\s*$"
                         r"|^\s*(上記|以上)の?(すべて|全て|いずれ)|どれでもない|いずれも(当てはまらない|正しくない)")


def numbers(text: str) -> set[float]:
    """The values of the numbers in an English text: '1,200' -> 1200, '£85m' / '85 million' -> 85000000,
    'two' -> 2 ('one' is left out: 'one of the ...')."""
    toks = re.findall(r"\d[\d,]*(?:\.\d+)?(?:bn|mn|tn|m|k)?\b|[a-z]+", text.translate(_FW).lower().replace("’", "'"))
    out: set[float] = set()
    i = 0
    while i < len(toks):
        t = toks[i]
        m = re.fullmatch(r"(\d[\d,]*(?:\.\d+)?)(bn|mn|tn|m|k)?", t)
        v = None
        if m:
            v = float(m.group(1).replace(",", "")) * MULTIPLIERS.get(m.group(2) or "", 1.0)
        elif t in NUMBER_WORDS and t != "one":
            v = NUMBER_WORDS[t]
        if v is not None:
            if i + 1 < len(toks) and toks[i + 1] in MULTIPLIERS and toks[i + 1] not in ("m", "k"):
                v *= MULTIPLIERS[toks[i + 1]]
                i += 1
            out.add(v)
        i += 1
    return out


def ja_numbers(text: str) -> set[float]:
    """Values in a Japanese text: '1,200台' -> 1200, '8500万' -> 85000000, '1億2000万' -> 120000000 (and 100000000)."""
    out: set[float] = set()
    groups = re.findall(r"((?:\d[\d,]*(?:\.\d+)?[千万億兆]?)+)", text.translate(_FW))
    for g in groups:
        total = 0.0
        for num, unit in re.findall(r"(\d[\d,]*(?:\.\d+)?)([千万億兆]?)", g):
            v = float(num.replace(",", "")) * JA_UNITS.get(unit, 1.0)
            out.add(v)
            total += v
        out.add(total)
    return out


def _english_word(low: str) -> bool:
    """An ordinary English word (not a name): in the word book, or lower-case in the system dictionary."""
    global _DICT
    if _DICT is None:
        try:
            _DICT = {w.strip() for w in Path("/usr/share/dict/words").read_text().splitlines() if w[:1].islower()}
        except OSError:
            _DICT = set()
    return low in _DICT or low.removesuffix("s") in _DICT or bool(vocab_library().by_lemma(low))


_DICT: set[str] | None = None


def capitalised(sentence: str) -> list[tuple[str, bool]]:
    """Capitalised words with whether each starts the sentence."""
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", sentence)
    return [(w.replace("’", "'").removesuffix("'s"), i == 0) for i, w in enumerate(words) if w[0].isupper()]


def _lower_words(text: str) -> set[str]:
    return {w.removesuffix("'s") for w in re.findall(r"[a-z][a-z'\-]*", text.lower().replace("’", "'"))}


def _ngrams(words: list[str], n: int) -> set[tuple]:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def copied_run(narration: str, source: str) -> int:
    """Length of the longest word sequence the narration shares with the source."""
    a = re.findall(r"[a-z0-9]+", narration.lower())
    b = re.findall(r"[a-z0-9]+", source.lower())
    n = 0
    while n < len(a) and _ngrams(a, n + 1) & _ngrams(b, n + 1):
        n += 1
    return n


def check_facts(en: str, src_text: str, title: bool = False) -> list[str]:
    """Numbers, names, date words and quotations in one English sentence that the source does not have.
    In a headline (Title Case) every word is capitalised: there an ordinary English word is not taken for a name."""
    problems = []
    src_nums = numbers(src_text)
    for n in sorted(numbers(en) - src_nums):
        problems.append(f"number {n:g} is not in the source: {en!r}")
    src_lower = _lower_words(src_text)
    src_case = set(re.findall(r"[A-Za-z][A-Za-z'\-]*", src_text.replace("’", "'"))) | {w.removesuffix("'s") for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", src_text.replace("’", "'"))}
    for w, first in capitalised(en):
        if w in ALWAYS_OK or w in src_case:
            continue
        low = w.lower()
        if (first or title) and low not in DATE_WORDS and (low in FUNCTION_WORDS or low in src_lower or _english_word(low)):
            continue
        problems.append(f"name or date {w!r} is not in the source: {en!r}")
    for low in _lower_words(en) & DATE_WORDS:
        if low not in src_lower:
            problems.append(f"date word {low!r} is not in the source: {en!r}")
    if re.search(r"[\"“”]", en):
        problems.append(f"quotation marks are not allowed: {en!r}")
    return problems


def _ja_numbers_ok(en: str, ja: str) -> bool:
    """Every number in the English sentence keeps its value in the translation ('£85m' -> '8500万ポンド')."""
    return numbers_digits(en) <= ja_numbers(ja)


def numbers_digits(text: str) -> set[float]:
    """Only numbers written with digits (a translation may write 'two' as 2つ or 二つ)."""
    return {v for v in numbers(re.sub(r"\b[a-z]+\b", lambda m: m.group(0) if m.group(0) in MULTIPLIERS else " ", text.lower()))}


def _word_entry(v: dict, level_sentences: list[dict], tier: int, topic: str) -> dict:
    s = level_sentences[v["sentence"]]
    t = v.get("transfer") or {}
    return {"id": f"news-{v['lemma']}", "lemma": v["lemma"], "pos": v["pos"], "meaning": v["meaning"], "tier": tier,
            "topic": topic, "scene": "ニュース", "source": "news",
            "examples": [{"role": "study", "en": s["en"], "form": v["form"], "ja": s["ja"]},
                         {"role": "transfer", "en": t.get("en", ""), "form": t.get("form", ""), "ja": t.get("ja", "")}]}


def problem_kinds(problems: list[str]) -> set[str]:
    """fact / translation / question / format / vocab / insufficient for the problems verify() found."""
    out = set()
    for p in problems:
        if p == "insufficient_evidence":
            out.add("insufficient")
        elif "translation" in p:
            out.add("translation")
        elif "copied" in p:
            out.add("copy")             # too close to the source's wording
        elif re.search(r"number|name or date|date word|quotation", p):
            out.add("fact")
        elif "question" in p:
            out.add("question")
        elif "vocabulary" in p:
            out.add("vocab")
        else:
            out.add("format")
    return out


def verify(obj: dict, src: dict, levels=LEVELS) -> tuple[dict, list[str], list[str]]:
    """Returns (clean lesson data, fatal problems, notes). Fatal problems reject the lesson; notes (e.g. a
    dropped vocabulary entry) do not."""
    problems: list[str] = []
    notes: list[str] = []
    src_text = source_text(src)
    if not isinstance(obj, dict) or obj.get("status") != "ok":
        return {}, ["insufficient_evidence" if isinstance(obj, dict) and obj.get("status") == "insufficient_evidence"
                    else "unexpected response"], notes
    data = {"title_en": str(obj.get("title_en") or "").strip()[:140], "title_ja": str(obj.get("title_ja") or "").strip()[:140],
            "summary_ja": str(obj.get("summary_ja") or "").strip()[:400], "levels": {}, "vocabulary": []}
    if not data["title_en"] or not _JA.search(data["title_ja"]) or not _JA.search(data["summary_ja"]):
        problems.append("title_en, title_ja and summary_ja are required (ja in Japanese)")
    problems += [f"title: {p}" for p in check_facts(data["title_en"], src_text, title=True)]
    want_levels = [lv for lv in LEVELS if lv in levels]
    levels = obj.get("levels") or {}
    avg = {}
    got = levels
    for lv in want_levels:
        L = got.get(lv) or {}
        sents = [{"en": lessons._plain(str(s.get("en") or "")), "ja": str(s.get("ja") or "").strip()}
                 for s in (L.get("sentences") or []) if isinstance(s, dict)]
        lo, hi = SENTENCES[lv]
        if not lo <= len(sents) <= hi:
            problems.append(f"{lv}: {len(sents)} sentences (needs {lo}-{hi})")
        for s in sents:
            n = len(s["en"].split())
            if not 3 <= n <= MAX_WORDS[lv]:
                problems.append(f"{lv}: sentence of {n} words (max {MAX_WORDS[lv]}): {s['en']!r}")
            if _JA.search(s["en"]) or not _JA.search(s["ja"]):
                problems.append(f"{lv}: en must be English and ja Japanese: {s['en']!r}")
            elif not _ja_numbers_ok(s["en"], s["ja"]):
                problems.append(f"{lv}: the translation changes a number: {s['en']!r} / {s['ja']!r}")
            problems += [f"{lv}: {p}" for p in check_facts(s["en"], src_text)]
        narration = " ".join(s["en"] for s in sents)
        run = copied_run(narration, src_text)
        if run > MAX_COPY_NGRAM:
            problems.append(f"{lv}: {run} words in a row are copied from the source (max {MAX_COPY_NGRAM})")
        qs = []
        for q in (L.get("questions") or [])[:QUESTIONS + 1]:
            try:
                choices = [str(c).strip() for c in q["choices"]]
                ans, ev = int(q["answer"]), [int(i) for i in q.get("evidence") or []]
            except (KeyError, TypeError, ValueError):
                problems.append(f"{lv}: malformed question")
                continue
            if (len(choices) != 4 or len(set(choices)) != 4 or not all(choices) or not 0 <= ans < 4
                    or not _JA.search(str(q.get("q") or "")) or not all(_JA.search(c) for c in choices)):
                problems.append(f"{lv}: question needs 4 different Japanese choices and one answer: {q.get('q')!r}")
                continue
            if not ev or not all(0 <= i < len(sents) for i in ev):
                problems.append(f"{lv}: question evidence must point to its sentences: {q.get('q')!r}")
                continue
            if any(META_CHOICE.search(c) for c in choices):
                problems.append(f"{lv}: 'all/none of the above' choices are not allowed: {q.get('q')!r}")
                continue
            qs.append({"q": str(q["q"]).strip(), "choices": choices, "answer": ans, "evidence": ev,
                       "explanation": str(q.get("explanation") or "").strip()[:300]})
        if len(qs) != QUESTIONS:
            problems.append(f"{lv}: {len(qs)} valid questions (needs {QUESTIONS})")
        data["levels"][lv] = {"sentences": sents, "questions": qs[:QUESTIONS]}
        avg[lv] = sum(len(s["en"].split()) for s in sents) / max(1, len(sents))
    if avg and not avg.get("A2", 0) < avg.get("B2", 99):
        notes.append(f"level sentence lengths are not graded: {avg}")
    # vocabulary: each entry must pass the word-book checks; a bad entry is dropped, not the lesson
    lib = vocab_library()
    vl = vocab_level(want_levels)
    data["vocab_level"] = vl
    b1 = data["levels"].get(vl, {}).get("sentences") or []
    narr_tokens = set(re.findall(r"[a-z]+", " ".join(s["en"] for lv in data["levels"].values() for s in lv["sentences"]).lower()))
    names = {w for s in b1 for w, first in capitalised(s["en"]) if not first}
    seen = set()
    for v in obj.get("vocabulary") or []:
        try:
            v = {"lemma": str(v["lemma"]).strip().lower(), "pos": str(v["pos"]).strip(), "meaning": str(v["meaning"]).strip(),
                 "form": str(v["form"]).strip(), "sentence": int(v["sentence"]),
                 "transfer": {k: lessons._plain(str((v.get("transfer") or {}).get(k) or "")) if k != "ja"
                              else str((v.get("transfer") or {}).get(k) or "").strip() for k in ("en", "form", "ja")}}
        except (KeyError, TypeError, ValueError):
            notes.append("vocabulary: malformed entry dropped")
            continue
        why = None
        if v["lemma"] in seen or v["pos"] not in POS or not 0 <= v["sentence"] < len(b1):
            why = "duplicate, unknown part of speech or sentence index"
        else:
            entry = _word_entry(v, b1, LEVEL_TIER[vl], src["category"])
            errs = validate_dataset({"dataset": {"license": "x", "source": "x"}, "words": [entry]}, min_words=0)
            t = v["transfer"]["en"]
            t_tokens = set(re.findall(r"[a-z]+", t.lower()))
            if errs:
                why = errs[0]
            elif len(t_tokens & narr_tokens) / max(1, len(t_tokens)) > 0.6:
                why = "the unseen sentence is too close to the news text"
            elif any(n in t for n in names):
                why = "the unseen sentence mentions a name from the news"
            elif not 8 <= len(t.split()) <= 18:
                why = "the unseen sentence must have 8-18 words"
        if why:
            notes.append(f"vocabulary {v['lemma']!r} dropped: {why}")
            continue
        seen.add(v["lemma"])
        book = lib.by_lemma(v["lemma"])
        data["vocabulary"].append({**v, "word_id": book.id if book else None})
    if len(data["vocabulary"]) < 2:
        problems.append(f"only {len(data['vocabulary'])} usable vocabulary entries (needs 2 or more)")
    return data, problems, notes


def transfer_checks(data: dict, seed: str) -> list[dict]:
    """The unseen-sentence questions: 'which meaning does <word> have here?' with 3 wrong meanings from the word
    book (never a meaning that could also be right)."""
    rng = random.Random(seed)
    lib = vocab_library()
    pool = list(lib.words.values())
    out = []
    for v in data["vocabulary"][:TRANSFER_CHECKS]:
        same_pos = [w for w in pool if w.pos == v["pos"] and w.lemma != v["lemma"] and not sense_matches(v["meaning"], w.meaning)
                    and not sense_matches(w.meaning, v["meaning"])]
        rng.shuffle(same_pos)
        wrong = []
        for w in same_pos:
            if w.meaning not in wrong and w.meaning != v["meaning"]:
                wrong.append(w.meaning)
            if len(wrong) == 3:
                break
        if len(wrong) < 3:
            continue
        choices = wrong + [v["meaning"]]
        rng.shuffle(choices)
        out.append({"lemma": v["lemma"], "en": v["transfer"]["en"], "ja": v["transfer"]["ja"], "form": v["transfer"]["form"],
                    "choices": choices, "answer": choices.index(v["meaning"])})
    return out


# --- 5. audio ----------------------------------------------------------------------------------------

CURRENCY = {"£": "pounds", "$": "dollars", "€": "euros", "¥": "yen"}
SCALE_WORD = {"bn": "billion", "m": "million", "mn": "million", "k": "thousand", "tn": "trillion",
              "billion": "billion", "million": "million", "thousand": "thousand", "trillion": "trillion"}


def spoken(text: str) -> str:
    """What the speech engine is given (and what speech recognition is compared with): '£85m' is read as
    '85 million pounds', '$2bn' as '2 billion dollars', '10%' as '10 percent'. The text on screen is unchanged."""
    def money(m: re.Match) -> str:
        scale = SCALE_WORD.get((m.group(3) or "").lower(), "")
        return " ".join(x for x in (m.group(2), scale, CURRENCY[m.group(1)]) if x)
    text = re.sub(r"([£$€¥])\s?(\d[\d,]*(?:\.\d+)?)(?:\s?(bn|mn|tn|m|k|billion|million|thousand|trillion)\b)?", money, text)
    return re.sub(r"(\d)\s?%", r"\1 percent", text)


def lesson_voices(lesson_id: str) -> tuple[str, str]:
    """A lesson's narrator and the (different) voice of its unseen sentences, fixed by the lesson id."""
    i = int(hashlib.sha1(lesson_id.encode()).hexdigest(), 16) % len(VOICES)
    return VOICES[i], VOICES[(i + 1) % len(VOICES)]


def audio_hash(text: str, voice: str, rate: int) -> str:
    return hashlib.sha1(json.dumps([text, voice, rate, "say", GENERATOR_VERSION]).encode()).hexdigest()[:16]


def _concat(parts: list[Path], out: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in parts:
            f.write(f"file '{p.resolve()}'\n")
        lst = f.name
    try:
        # re-encoded (not stream-copied): joined mp3 frames otherwise carry broken timestamps
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-ac", "1", "-b:a", "64k",
                        str(out)], check=True, timeout=120)
    finally:
        os.unlink(lst)


def asr_check(items: list[tuple[str, Path]]) -> list[bool | None]:
    """True / False per (text, file); None when speech recognition is not installed."""
    ok, _ = asr.available()
    if not ok or not items:
        return [None] * len(items)
    res = asr.transcribe([p for _, p in items], timeout=120 + 6 * len(items))
    out = []
    for (text, _), r in zip(items, res):
        ref, hyp = same_speech(asr.tokens(text), asr.tokens(r.get("text", "")))
        out.append(asr.wer(ref, hyp) <= 0.35)
    return out


def _us(t: str) -> str:
    """British and American spellings are the same speech (centre / center, colour / color)."""
    for uk, us in (("tres", "ters"), ("tre", "ter"), ("ours", "ors"), ("our", "or")):
        if t.endswith(uk) and len(t) > len(uk) + 2:
            return t[: -len(uk)] + us
    return t


def same_speech(ref: list[str], hyp: list[str]) -> tuple[list[str], list[str]]:
    """Spelling-only differences removed: 'EcoDataCenter' heard as 'Eco Data Center', 'datacentre' as 'data center'."""
    ref = [_us(t) for t in ref]
    hyp = [_us(t) for t in hyp]

    def join(a: list[str], want: set[str]) -> list[str]:
        out, i = [], 0
        while i < len(a):
            for n in (3, 2):
                if i + n <= len(a) and "".join(a[i:i + n]) in want:
                    out.append("".join(a[i:i + n]))
                    i += n
                    break
            else:
                out.append(a[i])
                i += 1
        return out
    hyp = join(hyp, set(ref))
    ref = join(ref, set(hyp))
    return ref, hyp


def build_audio(lesson_id: str, date: str, data: dict, checks: list[dict]) -> dict:
    """Synthesize (or reuse) every sentence, check it by speech recognition, retry a failed one once with another
    voice, and join each level's sentences into one file. Levels / unseen sentences that fail are not offered."""
    folder = Path("news") / date / lesson_id
    root = config.AUDIO_DIR / folder
    root.mkdir(parents=True, exist_ok=True)
    meta_path = root / "metadata.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    narrator, other = lesson_voices(lesson_id)

    def say(rel: Path, text: str, voice: str, rate: int) -> float:
        text = spoken(text)
        h = audio_hash(text, voice, rate)
        m = meta.get(str(rel)) or {}
        if m.get("hash") == h and (config.AUDIO_DIR / rel).exists():
            return m["seconds"]
        secs, used = tts._say_checked(text, voice, rate, config.AUDIO_DIR / rel)
        meta[str(rel)] = {"hash": h, "seconds": round(secs, 2), "voice": used, "text": text}
        return secs

    jobs = []   # (key, text, rel, voice, rate)
    lesson_levels = [lv for lv in LEVELS if lv in data["levels"]]
    for lv in lesson_levels:
        for i, s in enumerate(data["levels"][lv]["sentences"]):
            jobs.append(((lv, i), s["en"], folder / f"{lv}-{i}.mp3", narrator, LEVEL_RATE[lv]))
    for c in checks:
        jobs.append((("t", c["lemma"]), c["en"], folder / f"t-{c['lemma']}.mp3", other, LEVEL_RATE["B1"]))
    secs, failed = {}, set()
    for key, text, rel, voice, rate in jobs:
        try:
            secs[key] = say(rel, text, voice, rate)
        except RuntimeError:
            failed.add(key)
    heard = dict(zip([j[0] for j in jobs if j[0] not in failed],
                     asr_check([(spoken(j[1]), config.AUDIO_DIR / j[2]) for j in jobs if j[0] not in failed])))
    retry = [j for j in jobs if heard.get(j[0]) is False]
    for key, text, rel, voice, rate in retry:              # once more with another voice
        alt = next(v for v in VOICES if v != voice)
        try:
            secs[key] = say(rel, text, alt, rate)
        except RuntimeError:
            failed.add(key)
    if retry:
        again = asr_check([(spoken(j[1]), config.AUDIO_DIR / j[2]) for j in retry if j[0] not in failed])
        heard.update(zip([j[0] for j in retry if j[0] not in failed], again))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    out = {"voice": narrator, "transfer_voice": other, "levels": {}, "transfer": {}}
    for lv in lesson_levels:
        keys = [(lv, i) for i in range(len(data["levels"][lv]["sentences"]))]
        if any(k in failed for k in keys):
            out["levels"][lv] = {"status": "failed", "reason": "音声を作れませんでした"}
            continue
        states = [heard.get(k) for k in keys]
        if any(s is False for s in states):
            out["levels"][lv] = {"status": "failed", "reason": "音声認識が英文と一致しませんでした"}
            continue
        if any(s is None for s in states):
            out["levels"][lv] = {"status": "unchecked", "reason": "音声認識が使えないため未照合"}
            continue
        rels = [folder / f"{lv}-{i}.mp3" for i in range(len(keys))]
        full = folder / f"{lv}.mp3"
        h = hashlib.sha1("".join(meta[str(r)]["hash"] for r in rels).encode()).hexdigest()[:16]
        if (meta.get(str(full)) or {}).get("hash") != h or not (config.AUDIO_DIR / full).exists():
            _concat([config.AUDIO_DIR / r for r in rels], config.AUDIO_DIR / full)
            meta[str(full)] = {"hash": h}
        out["levels"][lv] = {"status": "ok", "full": str(full), "sentences": [str(r) for r in rels],
                             "seconds": round(sum(secs[k] for k in keys), 1)}
    for c in checks:
        k = ("t", c["lemma"])
        if k not in failed and heard.get(k):
            out["transfer"][c["lemma"]] = str(folder / f"t-{c['lemma']}.mp3")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return out


# --- 6. one lesson / one run ---------------------------------------------------------------------------

class BudgetExceeded(NewsError):
    def __init__(self, message: str):
        super().__init__(402, message, kind="budget")


def raw_dir(lid: str) -> Path:
    """Raw Gemini requests / responses for checking (no key: it is sent as a header and never stored)."""
    return config.DATA_DIR / "news_raw" / lid


def _keep_raw(lid: str, n: int, prompt_text: str, raw: dict | None, outcome: dict) -> None:
    d = raw_dir(lid)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"call-{n}.json").write_text(json.dumps({"at": db.now_iso(), "prompt": prompt_text, **(raw or {}), "outcome": outcome},
                                                 ensure_ascii=False, indent=1))


def _count(usage: dict, use: dict) -> None:
    usage["calls"] = usage.get("calls", 0) + 1
    usage["prompt"] = usage.get("prompt", 0) + use.get("prompt", 0)
    usage["output"] = usage.get("output", 0) + use.get("output", 0)
    usage["thoughts"] = usage.get("thoughts", 0) + use.get("thoughts", 0)
    usage["usd"] = round(usage.get("usd", 0.0) + cost(use), 6)
    if use.get("model"):
        usage["model"] = use["model"]          # the model behind the "-latest" alias


def make_lesson(src: dict, date: str, usage: dict, levels=LEVELS, budget_usd: float | None = None) -> dict:
    """Generate, verify (one regeneration when the cause can be fixed), build audio and publish one story.

    Failures are recorded with their kind. Retryable (later runs): api (no answer), audio (made again from the saved
    text, without Gemini). Permanent: the check failed twice, the source is not enough, the answer was cut off at
    the output limit or blocked. A story never gets more than MAX_CALLS_PER_STORY Gemini calls in total."""
    lid = "nw-" + hashlib.sha1(src["id"].encode()).hexdigest()[:10]
    now = db.now_iso()
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM news_lessons WHERE source_id=?", (src["id"],)).fetchone()
        if row and row["status"] not in ("draft", "retry"):
            return {"id": lid, "status": row["status"], "skipped": True}
        calls, tries = (row["calls"], row["tries"]) if row else (0, 0)
        saved = json.loads(row["data"]) if row and row["failure"] == "audio" and row["data"] else None
        conn.execute("INSERT INTO news_lessons (id, date, source_id, category, status, created_at, updated_at, calls, tries)"
                     " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status='draft', updated_at=excluded.updated_at,"
                     " tries=news_lessons.tries+1",
                     (lid, date, src["id"], src["category"], "draft", now, now, 0, 1))
        tries += 1
    source = {k: src[k] for k in ("id", "feed", "publisher", "category", "kind", "url", "title", "published_at")}
    notes: list[str] = []
    if saved:                                            # audio failed last time: only the audio is made again
        data = saved
    elif calls >= MAX_CALLS_PER_STORY:
        _fail(lid, "rejected", "calls", "この記事は Gemini の呼び出し上限に達しました", calls, {"source": source}, {})
        return {"id": lid, "status": "rejected", "kind": "calls", "reason": "calls"}
    else:
        problems: list[str] = []
        data: dict = {}
        for attempt in range(min(2, MAX_CALLS_PER_STORY - calls)):
            text = prompt(src, problems if attempt else None, levels)
            if budget_usd is not None and usage.get("usd", 0.0) + worst_case_cost(text, lessons.gemini_model()) > budget_usd:
                _fail(lid, "retry" if attempt == 0 else "rejected", "budget", "予算を超える見込みのため Gemini を呼びませんでした",
                      calls, {"source": source, **data}, {"problems": problems})
                raise BudgetExceeded(f"予算 ${budget_usd:.2f} を超える見込みのため停止しました（使用済み ${usage.get('usd', 0):.4f}）")
            try:
                obj, use = call_gemini(text, levels)
            except NewsError as e:
                billed = bool(e.usage)
                if billed:
                    _count(usage, e.usage)
                    calls += 1
                _keep_raw(lid, calls if billed else calls + 1, text, e.raw, {"kind": e.kind, "message": e.message})
                if e.kind == "json" and attempt == 0 and calls < MAX_CALLS_PER_STORY:
                    problems = ["the previous answer was not valid JSON"]
                    continue                              # sporadic: one more try
                retry = (e.kind in RETRYABLE or e.kind == "auth") and tries < MAX_TRIES_PER_STORY   # a key problem is not the story's
                _fail(lid, "retry" if retry else "rejected", e.kind, e.message, calls, {"source": source}, {"problems": [e.message]})
                if e.kind in ("auth", "api"):
                    raise                                 # the run stops (key / quota / network)
                return {"id": lid, "status": "retry" if retry else "rejected", "kind": e.kind, "reason": e.message}
            _count(usage, use)
            calls += 1
            data, problems, notes = verify(obj, src, levels)
            kinds = problem_kinds(problems)
            _keep_raw(lid, calls, text, use.get("raw"), {"problems": problems, "notes": notes, "kinds": sorted(kinds)})
            if not problems or not kinds <= FIXABLE:
                break
        if problems:
            kinds = problem_kinds(problems)
            status = "insufficient" if kinds == {"insufficient"} else "rejected"
            _fail(lid, status, ",".join(sorted(kinds)), problems[0][:300], calls, {"source": source, **data},
                  {"problems": problems, "notes": notes})
            return {"id": lid, "status": status, "kind": ",".join(sorted(kinds)), "reason": problems[0]}
        data["source"] = source
        data["corroborated"] = src.get("corroborated") or []
        data["transfer_checks"] = transfer_checks(data, lid)
    audio = build_audio(lid, date, data, data["transfer_checks"])
    ok_levels = [lv for lv, a in audio["levels"].items() if a["status"] == "ok"]
    checks = {"problems": [], "notes": notes, "audio": {lv: a["status"] for lv, a in audio["levels"].items()}}
    if not ok_levels:
        reason = "; ".join(f"{lv}: {a['reason']}" for lv, a in audio["levels"].items())
        retry = tries < MAX_TRIES_PER_STORY
        _fail(lid, "retry" if retry else "rejected", "audio", reason, calls, data, checks)   # the text is kept
        return {"id": lid, "status": "retry" if retry else "rejected", "kind": "audio", "reason": reason}
    published = {**data, "transfer_checks": [c for c in data["transfer_checks"] if c["lemma"] in audio["transfer"]]}
    with db.tx() as conn:
        conn.execute("UPDATE news_lessons SET status='published', data=?, checks=?, audio=?, reason=NULL, failure=NULL,"
                     " retry_after=NULL, calls=?, model=?, updated_at=? WHERE id=?",
                     (json.dumps(published, ensure_ascii=False), json.dumps(checks, ensure_ascii=False), json.dumps(audio),
                      calls, usage.get("model") or lessons.gemini_model(), db.now_iso(), lid))
    return {"id": lid, "status": "published", "levels": ok_levels}


def _fail(lid, status, kind, reason, calls, data, checks) -> None:
    wait = dt.timedelta(0) if kind == "budget" else RETRY_AFTER      # no call was made: nothing to wait for
    after = (dt.datetime.now(dt.timezone.utc) + wait).isoformat(timespec="seconds") if status == "retry" else None
    with db.tx() as conn:
        conn.execute("UPDATE news_lessons SET status=?, failure=?, reason=?, retry_after=?, calls=?, data=?, checks=?,"
                     " model=?, updated_at=? WHERE id=?",
                     (status, kind, reason[:300], after, calls, json.dumps(data, ensure_ascii=False),
                      json.dumps(checks, ensure_ascii=False), lessons.gemini_model(), db.now_iso(), lid))


def settings() -> dict:
    s = db.profile()["settings"]
    topics = [t for t in (s.get("news_topics") or []) if t in F.CATEGORIES] or ["technology"]
    levels = [lv for lv in LEVELS if lv in (s.get("news_levels") or LEVELS)] or list(LEVELS)
    return {"auto": bool(s.get("news_auto", True)), "hour": int(s.get("news_hour", 6)), "topics": topics,
            "per_day": max(1, min(5, int(s.get("news_per_day", 3)))), "levels": levels,
            "level": s.get("news_level") if s.get("news_level") in levels else vocab_level(levels)}


def run(trigger: str = "manual", date: str | None = None, opener=None, levels=None, budget_usd: float | None = None,
        count: int | None = None, only: str | None = None) -> dict:
    """Collect, select and make today's lessons. Safe to call again: it only fills what is missing.
    `budget_usd` stops before a call whose worst case would go over it; `only` makes one given story."""
    if not _run_lock.acquire(blocking=False):
        return {"status": "busy"}
    rid = uuid.uuid4().hex[:12]
    date = date or db.local_date()
    detail: dict = {"usage": {"calls": 0, "prompt": 0, "output": 0, "usd": 0.0}, "lessons": []}
    status = "failed"
    try:
        db.q("INSERT INTO news_runs (id, date, trigger, started_at, status) VALUES (?,?,?,?,?)",
             (rid, date, trigger, db.now_iso(), "running"))
        if not lessons.gemini_key():
            status = "no_key"
        else:
            detail["collect"] = collect(opener) if not only else None
            s = settings()
            levels = levels or s["levels"]
            want = 1 if only else count if count is not None else s["per_day"]
            have = db.q1("SELECT COUNT(*) n FROM news_lessons WHERE date=? AND status='published'", (date,))["n"]
            with db.tx() as conn:
                if only:
                    r = conn.execute("SELECT * FROM news_sources WHERE id=?", (only,)).fetchone()
                    picks = [{**dict(r), "corroborated": []}] if r else []
                else:
                    picks = select(conn, s["topics"], max(0, want - have))
            for src in picks:
                try:
                    detail["lessons"].append(make_lesson(src, date, detail["usage"], levels, budget_usd))
                except NewsError as e:
                    detail["lessons"].append({"source": src["id"], "status": "error", "kind": e.kind, "reason": e.message})
                    break             # budget, key, quota or network: stop instead of failing every story
            made = sum(1 for x in detail["lessons"] if x.get("status") == "published")
            errors = [x for x in detail["lessons"] if x.get("status") == "error"]
            if have + made >= want:
                status = "ok"
            elif errors and not made:
                status = "failed"
            elif not picks:
                status = "no_stories" if not (detail["collect"] or {}).get("errors") or (detail["collect"] or {}).get("found") else "failed"
            else:
                status = "partial"
        return {"id": rid, "status": status, **detail}
    except Exception as e:  # noqa: BLE001 - a run must always be closed with its error
        log.exception("news run failed")
        status = "failed"
        detail["error"] = f"{type(e).__name__}: {e}"[:300]
        return {"id": rid, "status": status, **detail}
    finally:
        db.q("UPDATE news_runs SET status=?, finished_at=?, detail=? WHERE id=?",
             (status, db.now_iso(), json.dumps(detail, ensure_ascii=False), rid))
        _run_lock.release()


# --- 7. every morning ---------------------------------------------------------------------------------

def auto_enabled() -> bool:
    return os.environ.get("LISTENFLOW_NEWS_AUTO", "1") != "0" and settings()["auto"]


def due(now: dt.datetime | None = None) -> bool:
    """Is a morning run due now? After `news_hour`, once a day; a failed run is retried after an hour, 3 times."""
    if not auto_enabled() or not lessons.gemini_key():
        return False
    now = now or dt.datetime.now().astimezone()
    if now.hour < settings()["hour"]:
        return False
    runs = db.q("SELECT status, started_at FROM news_runs WHERE date=? AND trigger='auto'", (now.date().isoformat(),))
    if any(r["status"] in ("ok", "partial", "no_stories", "running", "no_key") for r in runs):
        return False
    failed = [r for r in runs if r["status"] == "failed"]
    if len(failed) >= MAX_FAILED_RUNS:
        return False
    if failed and now - dt.datetime.fromisoformat(max(r["started_at"] for r in failed)) < dt.timedelta(hours=1):
        return False
    return True


def start_scheduler() -> None:
    global _started
    if _started:
        return
    _started = True
    stop = threading.Event()

    def loop():
        wait = 30                       # first look shortly after start-up (the Mac may have been asleep)
        while not stop.wait(wait):
            wait = RUN_EVERY_S
            try:
                if due():
                    run("auto")
            except Exception:  # noqa: BLE001 - the loop must survive
                log.exception("news scheduler")

    threading.Thread(target=loop, name="news-daily", daemon=True).start()


def status() -> dict:
    s = settings()
    last = db.q1("SELECT * FROM news_runs ORDER BY started_at DESC LIMIT 1")
    today = db.local_date()
    now = dt.datetime.now().astimezone()
    nxt = now.replace(hour=s["hour"], minute=0, second=0, microsecond=0)
    if due(now):
        nxt = now                       # within the next check (every 10 minutes)
    elif now >= nxt:
        nxt += dt.timedelta(days=1)
    month = (dt.date.today().replace(day=1)).isoformat()
    calls = {"calls": 0, "prompt": 0, "output": 0, "usd": 0.0}
    for r in db.q("SELECT detail FROM news_runs WHERE date>=? AND detail IS NOT NULL", (month,)):
        u = (json.loads(r["detail"]) or {}).get("usage") or {}
        for k in calls:
            calls[k] += float(u.get(k) or 0) if k == "usd" else int(u.get(k) or 0)
    calls["usd"] = round(calls["usd"], 4)
    return {"settings": s, "gemini": bool(lessons.gemini_key()), "auto": auto_enabled(), "running": _run_lock.locked(),
            "last_run": {k: last[k] for k in ("date", "trigger", "started_at", "finished_at", "status")} | {
                "detail": _run_summary(last["detail"])} if last else None,
            "next_run": nxt.isoformat(timespec="minutes") if auto_enabled() else None,
            "usage_month": calls, "categories": F.CATEGORIES, "levels": LEVEL_LABEL}


def _run_summary(detail: str | None) -> dict | None:
    if not detail:
        return None
    d = json.loads(detail)
    return {"collect": d.get("collect"), "error": d.get("error"),
            "lessons": [{k: x.get(k) for k in ("id", "status", "kind", "reason")} for x in d.get("lessons", [])]}
