"""Speech recognition worker, run in its own virtualenv (backend/.venv-asr, Python 3.12)
because faster-whisper's dependencies do not build on the API's Python.

stdin:  {"files": ["/abs/a.mp3", ...], "model": "base.en", "model_dir": "...", "language": "en"}
stdout: {"model": "...", "results": [{"file": ..., "text": ..., "duration": ...,
          "segments": [{"start", "end", "text", "avg_logprob", "no_speech_prob"}],
          "words": [{"word", "start", "end", "probability"}]}]}
Only reads the given files; writes nothing except the model cache in model_dir.
"""
import json
import subprocess
import sys

import numpy as np


def load(path: str) -> np.ndarray:
    """Decode with ffmpeg (already required by ListenFlow) to 16 kHz mono float32."""
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def main() -> None:
    req = json.load(sys.stdin)
    from faster_whisper import WhisperModel

    model = WhisperModel(req.get("model", "base.en"), device="cpu", compute_type="int8",
                         download_root=req.get("model_dir"))
    out = []
    for f in req["files"]:
        segs, info = model.transcribe(load(f), language=req.get("language", "en"), beam_size=5,
                                      word_timestamps=True, vad_filter=False, condition_on_previous_text=False)
        segments, words = [], []
        for s in segs:
            segments.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(),
                             "avg_logprob": round(s.avg_logprob, 4), "no_speech_prob": round(s.no_speech_prob, 4)})
            for w in s.words or []:
                words.append({"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
                              "probability": round(w.probability, 4)})
        out.append({"file": f, "duration": round(info.duration, 3), "text": " ".join(s["text"] for s in segments),
                    "segments": segments, "words": words})
    json.dump({"model": req.get("model", "base.en"), "results": out}, sys.stdout)


if __name__ == "__main__":
    main()
