"""Audio synthesis for teaching material.

Uses macOS `say` (multiple English voices) + FFmpeg. Each script line becomes one segment;
segments are concatenated with short pauses into the clip audio, and exact start/end offsets
are recorded in audio/manifest.json. Because the audio is generated from the script, the
transcript and the segment boundaries are correct by construction.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

SAMPLE_RATE = 22050
GAP_SECONDS = 0.6
_manifest_lock = threading.Lock()


def available() -> tuple[bool, str]:
    if not shutil.which("say"):
        return False, "macOS の `say` コマンドが見つかりません（音声生成は Mac 上でのみ可能です）"
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg が見つかりません（brew install ffmpeg）"
    return True, "ok"


def _clip_hash(raw: dict) -> str:
    payload = json.dumps({"lines": raw["lines"], "voices": raw.get("voices"), "level": raw["level"],
                          "rate": config.LEVEL_RATE.get(raw["level"], 175)}, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def _say_to_wav(text: str, voice: str, rate: int, out: Path) -> None:
    """Run `say`; it occasionally hangs when many instances run at once, so time out and retry."""
    cmd = ["say", "-v", voice, "-r", str(rate), "-o", str(out), f"--data-format={'LEI16'}@{SAMPLE_RATE}", text]
    last = None
    for attempt in range(4):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30)
            if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
                return
            last = r.stderr.decode(errors="ignore")
        except subprocess.TimeoutExpired:
            last = "timeout"
    raise RuntimeError(f"say failed for voice {voice}: {last}")


def _to_mp3(wav: Path, mp3: Path) -> None:
    mp3.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-ac", "1", "-b:a", "64k", str(mp3)],
                   check=True)


def synthesize_clip(raw: dict, speak=None) -> dict:
    """`speak(text, voice, rate, out_wav)` writes a mono 16-bit SAMPLE_RATE wav; default is macOS `say`."""
    speak = speak or _say_to_wav
    cid = raw["id"]
    rate = config.LEVEL_RATE.get(raw["level"], 175)
    voices = raw.get("voices", {})
    out_segments = []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        frames_all: list[bytes] = []
        silence = b"\x00\x00" * int(SAMPLE_RATE * GAP_SECONDS)
        lead = b"\x00\x00" * int(SAMPLE_RATE * 0.3)
        frames_all.append(lead)
        t = 0.3
        for i, (spk, text) in enumerate(raw["lines"]):
            wav_path = tdp / f"{i}.wav"
            speak(text, voices.get(spk, config.DEFAULT_VOICE), rate, wav_path)
            with wave.open(str(wav_path), "rb") as w:
                data = w.readframes(w.getnframes())
                dur = w.getnframes() / w.getframerate()
            seg_rel = f"segments/{cid}_{i}.mp3"
            # pad each segment a little so playback doesn't clip the first phoneme
            padded = tdp / f"{i}_p.wav"
            with wave.open(str(padded), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
                w.writeframes(lead + data + lead)
            _to_mp3(padded, config.AUDIO_DIR / seg_rel)
            out_segments.append({"audio": seg_rel, "start": round(t, 3), "end": round(t + dur, 3)})
            frames_all.append(data)
            frames_all.append(silence)
            t += dur + GAP_SECONDS
        full = tdp / "full.wav"
        with wave.open(str(full), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
            w.writeframes(b"".join(frames_all))
        clip_rel = f"clips/{cid}.mp3"
        _to_mp3(full, config.AUDIO_DIR / clip_rel)
    return {"audio": clip_rel, "duration": round(t, 3), "segments": out_segments, "hash": _clip_hash(raw)}


def load_manifest() -> dict:
    p = config.AUDIO_DIR / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_manifest(manifest: dict) -> None:
    p = config.AUDIO_DIR / "manifest.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    tmp.replace(p)


def build_all(raws: list[dict], force: bool = False, workers: int = 3, log=print) -> dict:
    config.ensure_dirs()
    manifest = load_manifest()
    # imported recordings and Gemini-voiced lessons cannot be re-created with `say`; never touch them here
    raws = [r for r in raws if r.get("source_kind") != "notebooklm" and r.get("voice_engine") != "gemini"]
    todo = [r for r in raws if force or manifest.get(r["id"], {}).get("hash") != _clip_hash(r)
            or not (config.AUDIO_DIR / manifest.get(r["id"], {}).get("audio", "missing")).exists()]
    log(f"{len(raws)} clips, {len(todo)} to synthesize")

    def work(raw):
        res = synthesize_clip(raw)
        with _manifest_lock:
            manifest[raw["id"]] = res
            save_manifest(manifest)
        log(f"  ✓ {raw['id']} ({res['duration']:.1f}s)")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, todo))
    return manifest


def synthesize_one(raw: dict, speak=None) -> dict:
    config.ensure_dirs()
    res = synthesize_clip(raw, speak)
    with _manifest_lock:
        manifest = load_manifest()
        manifest[raw["id"]] = res
        save_manifest(manifest)
    return res


# --- vocabulary audio ------------------------------------------------------------------
# One file for the word on its own (US voice, slow and clear) and one for its example sentence
# (rotating US / UK / AU voices, as in TOEIC). File names are hashes so the URL never reveals the word.

VOCAB_EXAMPLE_VOICES = ["Samantha", "Daniel", "Karen"]
VOCAB_WORD_RATE = 140
VOCAB_EXAMPLE_RATE = {1: 165, 2: 172, 3: 180, 4: 188}


def _vocab_hash(word, voice: str) -> str:
    payload = json.dumps([word.lemma, word.example, voice, VOCAB_WORD_RATE, VOCAB_EXAMPLE_RATE[word.tier]])
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def _say_to_mp3(text: str, voice: str, rate: int, mp3: Path) -> float:
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "raw.wav"
        _say_to_wav(text, voice, rate, raw)
        with wave.open(str(raw), "rb") as w:
            data = w.readframes(w.getnframes())
            dur = w.getnframes() / w.getframerate()
        lead = b"\x00\x00" * int(SAMPLE_RATE * 0.25)
        padded = Path(td) / "p.wav"
        with wave.open(str(padded), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
            w.writeframes(lead + data + lead)
        _to_mp3(padded, mp3)
    return dur


def build_vocab_audio(words, force: bool = False, workers: int = 3, log=print) -> dict:
    from .vocab_data import load_audio_manifest, manifest_path

    config.ensure_dirs()
    manifest = load_audio_manifest()
    voices = {w.id: VOCAB_EXAMPLE_VOICES[i % len(VOCAB_EXAMPLE_VOICES)] for i, w in enumerate(words)}

    def stale(w) -> bool:
        m = manifest.get(w.id) or {}
        return (force or m.get("hash") != _vocab_hash(w, voices[w.id])
                or not all((config.AUDIO_DIR / m.get(k, "missing")).exists() for k in ("word", "example")))

    todo = [w for w in words if stale(w)]
    log(f"{len(words)} words, {len(todo)} to synthesize")
    lock = threading.Lock()

    def save() -> None:
        p = manifest_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
        tmp.replace(p)

    def work(w) -> None:
        voice = voices[w.id]
        word_rel = f"vocab/{w.audio_key}-w.mp3"
        ex_rel = f"vocab/{w.audio_key}-x.mp3"
        wd = _say_to_mp3(w.lemma, config.DEFAULT_VOICE, VOCAB_WORD_RATE, config.AUDIO_DIR / word_rel)
        xd = _say_to_mp3(w.example, voice, VOCAB_EXAMPLE_RATE[w.tier], config.AUDIO_DIR / ex_rel)
        with lock:
            manifest[w.id] = {"word": word_rel, "example": ex_rel, "voice": voice, "hash": _vocab_hash(w, voice),
                              "word_seconds": round(wd, 2), "example_seconds": round(xd, 2)}
            save()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, todo))
    return manifest
