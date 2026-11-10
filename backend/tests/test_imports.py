"""NotebookLM-style audio import. Uses real ffmpeg + faster-whisper; skipped when speech recognition is absent."""
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app import asr, config


def _need_asr():
    if not asr.available()[0] or not shutil.which("ffmpeg"):
        pytest.skip("faster-whisper / ffmpeg not available")


@pytest.fixture()
def podcast(tmp_path):
    """~2.5 minutes of bundled speech concatenated into one m4a, plus its exact script."""
    _need_asr()
    from app.content import reload_library
    lib = reload_library()
    clips = [lib.clips[c] for c in ("tA-5", "tB-2", "tA-1", "tB-1", "tA-2", "tA-3", "tB-3", "tA-4")]
    lst = tmp_path / "list.txt"
    lst.write_text("".join(f"file '{config.AUDIO_DIR / c.audio}'\n" for c in clips))
    out = tmp_path / "overview.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "aac", str(out)], check=True)
    script = "\n".join(s.text for c in clips for s in c.segments)
    return out, script


def _wait(client, jid, secs=300):
    t = time.time()
    while time.time() - t < secs:
        j = client.get(f"/v1/imports/{jid}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(1)
    raise AssertionError("import did not finish")


def _upload(client, path, **form):
    with open(path, "rb") as f:
        return client.post("/v1/imports/notebooklm", files={"file": (path.name, f, "audio/mp4")}, data=form)


@pytest.fixture()
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path / "media")
    (tmp_path / "media").mkdir()
    return tmp_path / "media"


def test_import_with_script_keeps_every_script_word(client, podcast, media):
    from app.content import library
    path, script = podcast
    r = _upload(client, path, title="Overview", level="3", script=script)
    assert r.status_code == 200, r.text
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done", job
    assert job["info"]["checks"][0]["ok"]           # script agrees with the audio
    made = job["info"]["clips"]
    assert len(made) >= 2 and not job["info"]["skipped"]
    lib = library()
    texts = []
    for m in made:
        c = lib.clips[m["id"]]
        assert c.source == "user" and c.level == 3 and (media / c.audio).exists()
        assert 20 <= c.duration <= 150
        for s in c.segments:
            assert (media / s.audio).exists()
            assert 0 <= s.start < s.end <= c.duration + 0.5 and s.end - s.start <= 14
        texts += [s.text for s in c.segments]
        for q in c.questions:   # generated questions have exactly one right answer
            assert q["choices"].count(q["choices"][q["answer"]]) == 1
    # the script's wording is used verbatim, in order, with nothing lost or duplicated
    assert " ".join(texts).split() == script.split()
    # removing the import removes its clips and audio
    assert client.delete(f"/v1/imports/{job['id']}").status_code == 200
    assert not any(m["id"] in library().clips for m in made)
    assert not list((media / "segments").glob("u-nlm-*"))


def test_import_without_script_uses_recognised_text(client, podcast, media):
    from app.content import library
    path, script = podcast
    job = _wait(client, _upload(client, path, title="Overview").json()["id"])
    assert job["status"] == "done", job
    text = " ".join(s.text for m in job["info"]["clips"] for s in library().clips[m["id"]].segments)
    assert asr.wer(asr.tokens(script), asr.tokens(text)) <= 0.15


def test_import_rejects_wrong_script_and_non_audio(client, podcast, media, tmp_path):
    path, _ = podcast
    job = _wait(client, _upload(client, path, script="This script talks about something else entirely. " * 40).json()["id"])
    assert job["status"] == "failed" and "一致しません" in job["error"]
    assert not job["info"]["clips"]
    bad = tmp_path / "notes.m4a"
    bad.write_text("not audio")
    r = _upload(client, bad)
    assert r.status_code == 400
    assert not list(tmp_path.glob("**/*.upload"))   # rejected uploads are not left behind


def test_clip_without_questions_is_not_scored(client, media):
    """Imported audio with no TOEIC word to ask about is listening-only: it must not move the level."""
    from app import db, tts
    if not tts.available()[0]:
        pytest.skip("TTS not available")
    r = client.post("/library", json={"title": "No questions", "level": 2, "voices": {"A": "Samantha"},
                                      "lines": [{"speaker": "A", "text": "The weather was nice, so we walked to the park."},
                                                {"speaker": "A", "text": "Then we had lunch by the river."}]})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    client.post("/onboarding/skip", json={"level": 2})
    before = client.get("/profile").json()["level"]
    s = client.post("/sessions", json={"minutes": 15}).json()
    for _ in range(80):
        item = client.post(f"/sessions/{s['id']}/next", json={"elapsed": 1}).json()["item"]
        if item["type"] == "comprehension" and item["clip"]["id"] == cid:
            assert item["questions"] == []
            fb = client.post(f"/sessions/{s['id']}/answer", json={"item_id": item["item_id"], "answers": []}).json()
            assert fb["score"] is None
            break
        client.post(f"/sessions/{s['id']}/answer", json={"item_id": item["item_id"], "choice": 0, "text": "x", "rating": 2, "answers": [0, 0, 0]})
    else:
        raise AssertionError("the user clip was never served")
    assert not db.q1("SELECT 1 FROM attempts WHERE kind='comprehension' AND item_id=?", (cid,))
    assert client.get("/profile").json()["level"] == before
    assert db.q1("SELECT 1 FROM exposures WHERE kind='clip' AND item_id=?", (cid,))   # but it counts as heard
