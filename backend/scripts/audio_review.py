"""Listen to vocabulary audio that is on hold, and release it (or keep it out of the lessons).

    cd backend && .venv/bin/python -m scripts.audio_review            # list what is on hold
    cd backend && .venv/bin/python -m scripts.audio_review --play     # play each one and answer y / n / r / s
    cd backend && .venv/bin/python -m scripts.audio_review --ok clerk:example

Held audio (content/vocab/audio_review.json, status "hold") is not used in questions or lessons.
  y  the recording says the sentence correctly -> status "ok" (used from now on)
  n  it is wrong -> stays on hold
  r  re-read it with another voice (then run scripts.build_audio and listen again)
  s  skip for now
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

from app import config, tts
from app.vocab_data import AUDIO_REVIEW_PATH, spoken_text, vocab_library


def _load() -> dict:
    return json.loads(AUDIO_REVIEW_PATH.read_text()) if AUDIO_REVIEW_PATH.exists() else {"items": {}}


def _save(data: dict) -> None:
    tmp = AUDIO_REVIEW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(AUDIO_REVIEW_PATH)


def _set(data: dict, key: str, **fields) -> None:
    data["items"][key].update(fields, reviewed=dt.date.today().isoformat())


def main(argv: list[str]) -> int:
    lib = vocab_library()
    data = _load()
    held = lib.held_items()
    if "--ok" in argv:
        key = argv[argv.index("--ok") + 1]
        if key not in data["items"]:
            print(f"見つかりません: {key}")
            return 1
        _set(data, key, status="ok")
        _save(data)
        print(f"使用します: {key}")
        return 0
    if not held:
        print("保留中の音声はありません")
        return 0
    for key in held:
        wid, kind = key.split(":")
        w = lib.words[wid]
        rel = (lib.manifest.get(wid) or {}).get(kind)
        print(f"\n{key}  「{spoken_text(w, kind)}」\n  理由: {data['items'][key].get('reason', '')}\n  ファイル: {config.AUDIO_DIR / rel if rel else 'なし'}")
        if "--play" not in argv or not rel:
            continue
        ans = "p"
        while ans == "p":
            subprocess.run(["afplay", str(config.AUDIO_DIR / rel)], check=False)
            ans = input("  正しく読まれていますか？ [y]使う [n]保留のまま [r]別の声で作り直す [p]もう一度 [s]あとで > ").strip().lower()
            # e.g. "yy" when a key was also pressed during playback: ask again instead of silently skipping
            while ans not in ("y", "n", "r", "p", "s"):
                ans = input("  y / n / r / p / s のどれか1文字を入力してください > ").strip().lower()
        if ans == "y":
            _set(data, key, status="ok")
        elif ans == "n":
            _set(data, key, status="hold", heard_wrong=True)
        elif ans == "r":
            used = data["items"][key].get("voice") or (lib.manifest.get(wid) or {}).get(
                "voice" if kind == "example" else "transfer_voice")
            others = [v for v in tts.VOCAB_EXAMPLE_VOICES if v != used]
            _set(data, key, status="hold", voice=others[0])
            print(f"  次の scripts.build_audio で {others[0]} の声で作り直します")
        _save(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
