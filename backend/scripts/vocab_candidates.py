"""List word candidates from the official NGSL 1.2 / TSL 1.2 statistics files (content/vocab/sources).

    cd backend && .venv/bin/python -m scripts.vocab_candidates            # summary
    cd backend && .venv/bin/python -m scripts.vocab_candidates --missing  # candidates not yet in a dataset

Selection used for ngsl_tsl.json: TSL ranks 1-520 and NGSL ranks 1001-1520, single-word headwords of 3+
letters, minus words already in toeic_core.json. The Japanese glosses and sentences are written separately;
this script only reads headwords, ranks and SFI from the lists.
"""
from __future__ import annotations

import csv
import hashlib
import re
import sys

from app.vocab_data import VOCAB_DIR, vocab_library

SOURCES = VOCAB_DIR / "sources"
FILES = {
    "tsl": ("TSL_12_stats.csv", "Word", "TSL Rank", "2259df25a1077b04067ef0c0e9e4c98b46c91e4d539741dfaceba88651ff486d"),
    "ngsl": ("NGSL_12_stats.csv", "Lemma", "SFI Rank", "2098bab8955a120a9766c6282a51d7d578c6cb0a7d946600d2ffb73ba25a0b44"),
}
RANGES = {"tsl": (1, 520), "ngsl": (1001, 1520)}


def read_list(name: str) -> list[dict]:
    """Rows of one official list; refuses a file whose checksum differs from the recorded download."""
    fname, word_col, rank_col, sha = FILES[name]
    path = SOURCES / fname
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != sha:
        raise ValueError(f"{fname}: SHA-256 {digest} does not match the recorded download")
    out = []
    with path.open(encoding="latin-1", newline="") as f:     # the official files are not UTF-8
        for r in csv.DictReader(f):
            sfi = r.get("SFI", "").strip()
            out.append({"lemma": r[word_col].strip().lower(), "rank": int(r[rank_col]),
                        "sfi": float(sfi) if re.fullmatch(r"[\d.]+", sfi) else None, "source": name})
    return out


def candidates() -> list[dict]:
    out = []
    for name, (lo, hi) in RANGES.items():
        out += [r for r in read_list(name) if lo <= r["rank"] <= hi and re.fullmatch(r"[a-z]{3,}", r["lemma"])]
    return out


def main(argv: list[str]) -> int:
    lib = vocab_library()
    have = {w.lemma for w in lib.words.values()}
    cands = candidates()
    missing = [c for c in cands if c["lemma"] not in have]
    print(f"candidates {len(cands)} · in datasets {len(cands) - len(missing)} · not included {len(missing)}")
    if "--missing" in argv:
        for c in missing:
            print(f"{c['source']:5} {c['rank']:5} {c['lemma']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
