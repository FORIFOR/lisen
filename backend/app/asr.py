"""Speech recognition (faster-whisper) used to verify generated lesson audio and to align imported audio.

faster-whisper runs in a separate virtualenv (backend/.venv-asr) through scripts/asr_worker.py, so the API
keeps working when it is not installed: callers get `available() == (False, reason)` and must report the
check as not performed - never as passed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from . import config

MODEL = os.environ.get("LISTENFLOW_ASR_MODEL", "base.en")
WORKER = config.BACKEND_DIR / "scripts" / "asr_worker.py"


class AsrError(Exception):
    pass


def python() -> Path | None:
    p = os.environ.get("LISTENFLOW_ASR_PYTHON")
    cand = Path(p) if p else config.BACKEND_DIR / ".venv-asr" / "bin" / "python"
    return cand if cand.exists() else None


def model_dir() -> Path:
    return config.BACKEND_DIR / ".models"


def available() -> tuple[bool, str]:
    if os.environ.get("LISTENFLOW_ASR", "1") == "0":
        return False, "音声認識は無効化されています（LISTENFLOW_ASR=0）"
    if not python():
        return False, "faster-whisper が未導入です（./start.sh --setup-asr で導入）"
    return True, "ok"


def transcribe(files: list[Path], timeout: float = 600.0) -> list[dict]:
    ok, msg = available()
    if not ok:
        raise AsrError(msg)
    req = {"files": [str(Path(f).resolve()) for f in files], "model": MODEL, "model_dir": str(model_dir()), "language": "en"}
    try:
        r = subprocess.run([str(python()), str(WORKER)], input=json.dumps(req), capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, "HF_HUB_DISABLE_TELEMETRY": "1"})
    except subprocess.TimeoutExpired as e:
        raise AsrError("音声認識がタイムアウトしました") from e
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise AsrError(f"音声認識に失敗しました: {tail[0][:200]}")
    return json.loads(r.stdout)["results"]


# --- text comparison ------------------------------------------------------------------------

_NUM = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
        "8": "eight", "9": "nine", "10": "ten", "11": "eleven", "12": "twelve", "15": "fifteen", "20": "twenty",
        "30": "thirty"}


def tokens(text: str) -> list[str]:
    text = text.lower().replace("’", "'").replace("-", " ")
    out = []
    for t in re.findall(r"[a-z0-9']+", text):
        t = t.strip("'")
        if not t:
            continue
        out.append(_NUM.get(t, t))
    return out


def wer(ref: list[str], hyp: list[str]) -> float:
    """Word error rate (Levenshtein over words / reference length)."""
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)
