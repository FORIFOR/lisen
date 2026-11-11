"""Dictation scoring: normalization, token alignment, and error classification.

The reference text is ground truth (it is the exact script the audio was generated from),
so scoring never depends on speech recognition output.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

# --- categories -------------------------------------------------------------

SKILLS = {
    "weak_forms": "弱形（機能語）",
    "contractions": "短縮形",
    "linking": "音の連結",
    "numbers": "数字",
    "endings": "語尾 (-s / -ed)",
    "vocabulary": "語彙（内容語）",
    "comprehension": "内容理解",
}

FUNCTION_WORDS = set(
    """a an the and or but so of to for from at in on by with as than that this these those
    is are was were be been being am do does did have has had can could will would shall should
    may might must i you he she it we they me him her us them my your his its our their
    there here if then not no some any just up out about into over""".split()
)

CONTRACTIONS = {
    "i'm": ["i", "am"], "you're": ["you", "are"], "we're": ["we", "are"], "they're": ["they", "are"],
    "i've": ["i", "have"], "you've": ["you", "have"], "we've": ["we", "have"], "they've": ["they", "have"],
    "i'll": ["i", "will"], "you'll": ["you", "will"], "we'll": ["we", "will"], "they'll": ["they", "will"],
    "he'll": ["he", "will"], "she'll": ["she", "will"], "it'll": ["it", "will"], "there'll": ["there", "will"],
    "i'd": ["i", "would"], "you'd": ["you", "would"], "we'd": ["we", "would"], "they'd": ["they", "would"],
    "he'd": ["he", "would"], "she'd": ["she", "would"], "that'd": ["that", "would"],
    "don't": ["do", "not"], "doesn't": ["does", "not"], "didn't": ["did", "not"],
    "can't": ["can", "not"], "couldn't": ["could", "not"], "won't": ["will", "not"],
    "wouldn't": ["would", "not"], "shouldn't": ["should", "not"], "isn't": ["is", "not"],
    "aren't": ["are", "not"], "wasn't": ["was", "not"], "weren't": ["were", "not"],
    "haven't": ["have", "not"], "hasn't": ["has", "not"], "hadn't": ["had", "not"],
    "it's": ["it", "is"], "that's": ["that", "is"], "there's": ["there", "is"], "here's": ["here", "is"],
    "what's": ["what", "is"], "he's": ["he", "is"], "she's": ["she", "is"], "let's": ["let", "us"],
    "who's": ["who", "is"], "where's": ["where", "is"], "how's": ["how", "is"],
    "gonna": ["going", "to"], "wanna": ["want", "to"], "gotta": ["got", "to"],
}
# canonical forms of informal spellings so that "going to" == "gonna"
NUMBER_WORDS = {
    "zero": 0, "oh": None, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
ORDINALS = {"first": "1st", "second": "2nd", "third": "3rd", "fifth": "5th", "twelfth": "12th", "twentieth": "20th"}
VOWEL_START = re.compile(r"^[aeiou]")
CONSONANT_END = re.compile(r"[bcdfgklmnprstvxz]$")


@dataclass
class Tok:
    text: str            # canonical text used for comparison
    shown: str           # surface form for display
    contraction: bool = False
    index: int = 0       # index of the surface word in the original sentence


def _surface_words(text: str) -> list[str]:
    t = text.replace("’", "'").replace("‘", "'")
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)  # 1,200 -> 1200
    t = re.sub(r"(\d):(\d\d)", r"\1 \2", t)  # 7:30 -> 7 30
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1\2", t)
    t = t.replace("-", " ").replace("/", " ")
    t = re.sub(r"[^A-Za-z0-9' ]+", " ", t)
    return [w.strip("'") for w in t.split() if w.strip("'")]


def _collapse_numbers(words: list[str]) -> list[str]:
    """Convert runs of spelled-out number words into digits ("twenty five" -> "25")."""
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        if NUMBER_WORDS.get(w) is not None:
            total, current, j = 0, 0, i
            while j < len(words):
                x = words[j]
                if NUMBER_WORDS.get(x) is not None:
                    v = NUMBER_WORDS[x]
                    low = current % 100
                    # "seven thirty" (time) or "four eight" (digit sequence) start a new number
                    if low and (low % 10 != 0 or v >= 10 or low < 20):
                        break
                    current += v
                elif x in SCALES and (current or total):
                    current = max(current, 1) * SCALES[x]
                    if SCALES[x] >= 1000:
                        total += current
                        current = 0
                elif x == "and" and j + 1 < len(words) and NUMBER_WORDS.get(words[j + 1]) is not None \
                        and current % 100 == 0 and (current or total):
                    pass
                else:
                    break
                j += 1
            out.append(str(total + current))
            i = j
            continue
        out.append(ORDINALS.get(w, w))
        i += 1
    return out


def tokenize(text: str) -> list[Tok]:
    words = _collapse_numbers([w.lower() for w in _surface_words(text)])
    toks: list[Tok] = []
    for idx, w in enumerate(words):
        if w in CONTRACTIONS:
            for part in CONTRACTIONS[w]:
                toks.append(Tok(part, w, True, idx))
        elif w.endswith("'s"):
            toks.append(Tok(w[:-2], w, False, idx))
            toks.append(Tok("'s", w, True, idx))
        else:
            toks.append(Tok(w, w, False, idx))
    return toks


def _edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance (a swapped pair of letters counts as one edit)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            c = a[i - 1] != b[j - 1]
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s", "d"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def same_stem(a: str, b: str) -> bool:
    """True when two words differ only by an inflectional ending (walk/walked, magazine/magazines)."""
    if a == b:
        return False
    if _stem(a) == _stem(b):
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 3 and long_.startswith(short) and long_[len(short):] in ("s", "es", "d", "ed", "ing")


def is_typo(ref: str, hyp: str) -> bool:
    if ref.isdigit() or hyp.isdigit() or len(ref) < 5:
        return False
    if same_stem(ref, hyp):
        return False  # that's an ending error, not a typo
    return _edit_distance(ref, hyp) <= (1 if len(ref) < 8 else 2)


def classify(tok: Tok, prev: Tok | None, hyp: str | None = None) -> str:
    t = tok.text
    if t.isdigit() or re.fullmatch(r"\d+(st|nd|rd|th)", t):
        return "numbers"
    if tok.contraction:
        return "contractions"
    if hyp is not None and same_stem(hyp, t):
        return "endings"
    if t in FUNCTION_WORDS:
        return "weak_forms"
    if prev is not None and VOWEL_START.match(t) and CONSONANT_END.search(prev.text):
        return "linking"
    if hyp is None and re.search(r"(s|ed)$", t) and len(t) > 4:
        return "endings"
    return "vocabulary"


@dataclass
class DictationResult:
    accuracy: float
    correct: int
    total: int
    tokens: list[dict] = field(default_factory=list)  # per reference token: {text, status, category, typed}
    extra: list[str] = field(default_factory=list)
    category_stats: dict[str, list[int]] = field(default_factory=dict)  # cat -> [correct, total]
    errors: dict[str, int] = field(default_factory=dict)


def score_dictation(reference: str, typed: str) -> DictationResult:
    ref = tokenize(reference)
    hyp = tokenize(typed)
    n, m = len(ref), len(hyp)
    # DP alignment; typos count as cheap substitutions so they align
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            r, h = ref[i - 1].text, hyp[j - 1].text
            sub = 0 if r == h else (0.4 if is_typo(r, h) else (0.6 if same_stem(r, h) else 1))
            cost[i][j] = min(cost[i - 1][j] + 1, cost[i][j - 1] + 1, cost[i - 1][j - 1] + sub)
    # backtrace
    i, j = n, m
    aligned: list[tuple[int | None, int | None]] = []
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            r, h = ref[i - 1].text, hyp[j - 1].text
            sub = 0 if r == h else (0.4 if is_typo(r, h) else (0.6 if same_stem(r, h) else 1))
            if abs(cost[i][j] - (cost[i - 1][j - 1] + sub)) < 1e-9:
                aligned.append((i - 1, j - 1)); i -= 1; j -= 1; continue
        if i > 0 and abs(cost[i][j] - (cost[i - 1][j] + 1)) < 1e-9:
            aligned.append((i - 1, None)); i -= 1; continue
        aligned.append((None, j - 1)); j -= 1
    aligned.reverse()

    res = DictationResult(accuracy=0.0, correct=0, total=n)
    for ri, hi in aligned:
        if ri is None:
            res.extra.append(hyp[hi].shown)
            continue
        tok = ref[ri]
        prev = ref[ri - 1] if ri > 0 else None
        typed_tok = hyp[hi].text if hi is not None else None
        if typed_tok == tok.text:
            status = "correct"
        elif typed_tok is not None and is_typo(tok.text, typed_tok):
            status = "typo"  # heard correctly, spelling slip: counted as correct
        elif typed_tok is not None:
            status = "wrong"
        else:
            status = "missed"
        cat = classify(tok, prev, typed_tok if status == "wrong" else None)
        ok = status in ("correct", "typo")
        res.correct += ok
        stats = res.category_stats.setdefault(cat, [0, 0])
        stats[0] += ok
        stats[1] += 1
        if not ok:
            res.errors[cat] = res.errors.get(cat, 0) + 1
        res.tokens.append({"text": tok.text, "shown": tok.shown, "index": tok.index,
                           "status": status, "category": cat, "typed": hyp[hi].shown if hi is not None else None})
    res.accuracy = round(res.correct / n, 4) if n else 1.0
    return res


# --- discrimination (minimal pairs) ------------------------------------------

SWAPS = {
    # Only substitutions that preserve a plausible sentence without changing surrounding words.
    "will": "would", "would": "will",
    "this": "that", "that": "this", "these": "those", "those": "these", "i'll": "i'd", "i'd": "i'll",
    "we'll": "we'd", "we'd": "we'll", "you'll": "you'd", "you'd": "you'll",
}
SWAP_CATEGORY = {k: ("contractions" if "'" in k else "weak_forms") for k in SWAPS}


def _match_case(src: str, repl: str) -> str:
    return repl[0].upper() + repl[1:] if src[:1].isupper() else repl


def make_discrimination(text: str, prefer: str | None = None, rng: random.Random | None = None) -> dict | None:
    """Build a two-choice "which did you hear?" item from a sentence."""
    rng = rng or random.Random()
    words = text.split(" ")
    candidates: list[tuple[int, str, str, str]] = []  # (pos, original, replacement, category)
    for pos, raw in enumerate(words):
        core = re.sub(r"^[^A-Za-z']+|[^A-Za-z']+$", "", raw).replace("’", "'")
        low = core.lower()
        previous = re.sub(r"[^A-Za-z']", "", words[pos - 1]).lower() if pos else ""
        next_word = re.sub(r"[^A-Za-z']", "", words[pos + 1]).lower() if pos + 1 < len(words) else ""
        subject = previous in {"i", "you", "he", "she", "it", "we", "they", "this", "that"}
        safe = (low in {"these", "those"}
                or (low in {"this", "that"} and next_word in {"is", "was", "will", "would", "can", "could"})
                or (low in {"will", "would"} and subject and next_word in {"be", "have"})
                or (low in {"i'll", "i'd", "we'll", "we'd", "you'll", "you'd"} and
                    next_word == "have" and pos + 2 < len(words) and
                    re.sub(r"[^A-Za-z']", "", words[pos + 2]).lower() in {"to", "a", "the"}))
        if low in SWAPS and safe:
            candidates.append((pos, core, _match_case(core, SWAPS[low]), SWAP_CATEGORY[low]))
        number = re.search(r"(?<![A-Za-z0-9])\d+(?![A-Za-z0-9])", raw)
        if number and not re.search(r"\d+(?:st|nd|rd|th)\b", raw, re.I):
            value = int(number.group())
            if value > 1:
                other = value + (10 if value % 10 == 0 and 20 <= value < 90 else 1)
                candidates.append((pos, number.group(), str(other), "numbers"))
    if not candidates:
        return None
    if prefer:
        pref = [c for c in candidates if c[3] == prefer]
        if pref:
            candidates = pref
    pos, orig, repl, cat = rng.choice(candidates)
    alt_words = list(words)
    alt_words[pos] = words[pos].replace(orig, repl, 1)
    alt = " ".join(alt_words)
    options = [text, alt]
    rng.shuffle(options)
    return {"options": options, "answer": options.index(text), "target": orig, "distractor": repl,
            "position": pos, "category": cat}
