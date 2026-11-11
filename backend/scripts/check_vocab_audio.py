"""Check generated vocabulary audio with local speech recognition (faster-whisper; no network, no API).

    cd backend && .venv/bin/python -m scripts.check_vocab_audio              # 40 random words
    cd backend && .venv/bin/python -m scripts.check_vocab_audio --sample 200
    cd backend && .venv/bin/python -m scripts.check_vocab_audio --all

For each word it transcribes the study example and the unseen (transfer) sentence and compares them with the
text (word error rate), and checks that the target word is heard in each. The word read on its own is reported
separately: single words are often heard as a homophone (fare / fair), so a mismatch there is a warning.
Every file is also checked for a plausible length. Exit code 1 if any sentence fails.
"""
from __future__ import annotations

import json
import random
import sys

from app import asr, config, tts
from app.vocab_data import vocab_library

MAX_WER = 0.35


UNITS = {"km": "kilometers", "kms": "kilometers", "kg": "kilograms", "cm": "centimeters"}


def spelling(t: str) -> str:
    """Spelling-only differences are the same speech: UK/US (flavour/flavor, theatre/theater), apostrophes
    (recipient's / recipients), unit abbreviations (km)."""
    t = UNITS.get(t, t).replace("'", "")
    for uk, us in (("ours", "ors"), ("our", "or"), ("tres", "ters"), ("tre", "ter")):
        if t.endswith(uk) and len(t) > len(uk) + 2:
            return t[: -len(uk)] + us
    return t


def join_compounds(ref: list[str], hyp: list[str]) -> list[str]:
    """'business person' heard for 'businessperson' is the same speech: merge such adjacent pairs."""
    want = set(ref)
    out, i = [], 0
    while i < len(hyp):
        if i + 1 < len(hyp) and hyp[i] + hyp[i + 1] in want:
            out.append(hyp[i] + hyp[i + 1])
            i += 2
        else:
            out.append(hyp[i])
            i += 1
    return out


def main(argv: list[str]) -> int:
    ok, msg = asr.available()
    lib = vocab_library()
    words = [w for w in lib.words.values() if len(lib.files(w)) >= (3 if w.transfer else 2)]
    if "--words" in argv:
        wanted = set(argv[argv.index("--words") + 1].split(","))
        sample = [w for w in words if w.id in wanted]
    else:
        n = len(words) if "--all" in argv else int(argv[argv.index("--sample") + 1]) if "--sample" in argv else 40
        sample = random.Random(7).sample(words, min(n, len(words)))
    jobs = []
    for w in sample:
        a = lib.files(w)
        jobs.append((w, "word", w.lemma, a["word"]))
        jobs.append((w, "example", w.example, a["example"]))
        if w.transfer:
            jobs.append((w, "transfer", w.transfer, a["transfer"]))
    manifest = lib.manifest
    length_bad = []
    for w, kind, text, _rel in jobs:
        secs = manifest.get(w.id, {}).get(f"{kind}_seconds")
        if secs is not None and not tts.plausible_seconds(text, secs):
            length_bad.append(f"{w.id} {kind}: {secs}s for {len(text.split())} words")
    if not ok:
        print(f"音声認識が使えないため、長さの確認のみ: {msg}")
        print(f"checked {len(jobs)} files · length problems {len(length_bad)}")
        print("\n".join(length_bad))
        return 1 if length_bad else 0
    results = asr.transcribe([config.AUDIO_DIR / rel for _, _, _, rel in jobs], timeout=60 + 3 * len(jobs))
    failed, warned, failed_keys = list(length_bad), [], []
    for (w, kind, text, rel), res in zip(jobs, results):
        hyp = asr.tokens(res.get("text", ""))
        if kind == "word":
            if w.lemma.lower() not in join_compounds([w.lemma.lower()], hyp):
                warned.append(f"{w.id} word: heard {' '.join(hyp)!r}")
            continue
        ref = [spelling(t) for t in asr.tokens(text)]
        hyp = [spelling(t) for t in hyp]
        hyp = join_compounds(ref, hyp)
        ref = join_compounds(hyp, ref)                   # "stock broker" read as "stockbroker"
        err = asr.wer(ref, hyp)
        form = spelling((w.form if kind == "example" else w.transfer_form or w.lemma).lower())
        heard = set(hyp) | {t.removesuffix("s") for t in hyp}      # "the sender's name" contains "sender"
        if err > MAX_WER or not ({form, form.removesuffix("s")} & heard or any(form in t for t in hyp)):
            failed.append(f"{w.id} {kind}: WER {err:.2f} · {text!r} -> {' '.join(hyp)!r}")
            failed_keys.append(f"{w.id}:{kind}")
    sentences = sum(1 for j in jobs if j[1] != "word")
    print(f"checked {len(sample)} words · {sentences} sentences · failed {len(failed)} · single-word warnings {len(warned)}")
    for line in failed:
        print("FAIL", line)
    for line in warned:
        print("warn", line)
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        with open(out, "w") as f:
            json.dump({"failed": failed_keys, "checked": [w.id for w in sample]}, f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
