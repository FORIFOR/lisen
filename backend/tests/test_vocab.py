"""Vocabulary: dataset validation, adaptive diagnostic, per-modality mastery, FSRS review, lessons."""
import json
import random

import pytest

from app import db, lessons, vocab
from app.vocab_data import DATASET_PATH, validate_dataset, vocab_library


# --- phase 1: dataset ---------------------------------------------------------------------

def test_dataset_is_valid_and_licensed():
    data = json.loads(DATASET_PATH.read_text())
    assert validate_dataset(data) == []
    assert data["dataset"]["license"] and data["dataset"]["source"]
    # only values the dataset can back up: no invented CEFR / TOEIC statistics
    assert all(w["cefr_level"] is None and w["toeic_relevance"] is None for w in data["words"])
    assert not vocab_library().missing_audio(), "run scripts/build_audio.py"


def test_validation_catches_bad_entries():
    good = json.loads(DATASET_PATH.read_text())
    bad = json.loads(json.dumps(good))
    bad["words"].append(dict(bad["words"][0]))                         # duplicate
    bad["words"][1]["examples"][0]["en"] = "Nothing relevant here."     # word missing from example
    bad["words"][2]["meaning"] = "to assign"                           # gloss not Japanese
    bad["dataset"]["license"] = ""
    problems = "\n".join(validate_dataset(bad))
    for needle in ("duplicate id", "does not contain", "must be Japanese", "license"):
        assert needle in problems


def test_every_word_yields_single_answer_questions_in_all_modalities():
    rng = random.Random(1)
    lib = vocab_library()
    for w in lib.words.values():
        for mod in vocab.MODALITIES:
            p = vocab.build_item(w, mod, rng)
            assert p, (w.id, mod)
            opts = [lib.words[i] for i in p["secret"]["options"]]
            assert len({o.meaning for o in opts}) == 4
            assert opts[p["secret"]["answer"]].id == w.id
            others = [o for o in opts if o.id != w.id]
            assert not any(vocab.overlaps(w, o) for o in others), (w.id, [o.id for o in others])
            if mod == "context":
                # exactly one option's word is actually in the sentence that is played
                assert [o.id for o in opts if o.appears_in(w.example)] == [w.id]
            if mod != "text":
                assert w.lemma not in p["public"]["audio"].rsplit("/", 1)[-1]  # the file name must not give the word away
                assert "lemma" not in p["public"]


# --- phase 2: diagnostic ---------------------------------------------------------------------

def _answer_for(item_id, good):
    row = db.q1("SELECT payload FROM vocab_items WHERE id=?", (item_id,))
    sec = json.loads(row["payload"])["secret"]
    return sec, (sec["answer"] if good(sec) else (sec["answer"] + 1) % 4)


def run_diag(client, good):
    d = client.post("/v1/vocabulary/diagnostics", json={}).json()
    seen, steps = [], 0
    while True:
        r = client.post(f"/v1/vocabulary/diagnostics/{d['id']}/next").json()
        if r["done"]:
            return d["id"], r, seen
        item = r["item"]
        sec, choice = _answer_for(item["item_id"], good)
        seen.append(sec)
        fb = client.post("/v1/vocabulary/answers", json={"item_id": item["item_id"], "choice": choice, "ms": 2000})
        assert fb.status_code == 200, fb.text
        assert "meaning" not in fb.json(), "answers are not revealed during the diagnostic"
        steps += 1
        assert steps <= vocab.DIAG_MAX_ITEMS


def test_diagnostic_separates_reading_from_listening(client):
    """A learner who knows every word on paper but can't recognise tier>=2 words by ear."""
    lib = vocab_library()

    def good(sec):
        return sec["modality"] == "text" or lib.words[sec["word_id"]].tier == 1

    did, r, seen = run_diag(client, good)
    res = r["diagnostic"]["result"]
    assert res["items"] == vocab.DIAG_MAX_ITEMS
    assert res["modality"]["text"]["accuracy"] == 1.0
    assert res["modality"]["audio"]["accuracy"] < 0.7
    assert res["sound_gap"], "words known in writing but missed by ear must be identified"
    # a missed audio probe is followed by a text question for the same word (asked later, not right away)
    for i, sec in enumerate(seen):
        if sec["modality"] == "audio" and not good(sec):
            later = [j for j, s in enumerate(seen) if s["word_id"] == sec["word_id"] and s["modality"] == "text"]
            if later:
                assert later[0] - i >= 2
    # per-word state: text solid, audio weak -> "sound_gap", never "acquired"
    gap = res["sound_gap"][0]["id"]
    row = db.q1("SELECT * FROM user_vocab WHERE word_id=?", (gap,))
    assert vocab.status_of(row) == "sound_gap"
    # missed modalities became FSRS cards, known ones did not
    assert db.q1("SELECT 1 FROM srs_cards WHERE id=?", (f"w_audio:{gap}",))
    assert not db.q1("SELECT 1 FROM srs_cards WHERE id=?", (f"w_text:{gap}",))
    stats = client.get("/v1/vocabulary/stats").json()
    assert stats["tested_words"] == res["words"]
    assert stats["acquired"] == 0, "nothing is acquired before context listening is shown"
    assert stats["modality"]["text"]["mastery"] > stats["modality"]["audio"]["mastery"]


def test_diagnostic_staircase_moves_up_for_strong_learner(client):
    did, r, seen = run_diag(client, lambda sec: True)
    res = r["diagnostic"]["result"]
    assert res["estimated_tier"] >= 3
    assert max(vocab_library().words[s["word_id"]].tier for s in seen) == 4
    assert not res["weak"]


def test_diagnostic_resume_duplicate_and_conflict(client):
    d = client.post("/v1/vocabulary/diagnostics", json={}).json()
    a = client.post(f"/v1/vocabulary/diagnostics/{d['id']}/next").json()["item"]
    # another device resumes: same diagnostic, same pending question
    d2 = client.post("/v1/vocabulary/diagnostics", json={}).json()
    assert d2["id"] == d["id"] and d2["resumed"]
    b = client.post(f"/v1/vocabulary/diagnostics/{d['id']}/next").json()["item"]
    assert a["item_id"] == b["item_id"]
    body = {"item_id": a["item_id"], "choice": 0, "confidence": "guess"}
    first = client.post("/v1/vocabulary/answers", json=body)
    retry = client.post("/v1/vocabulary/answers", json=body)       # network retry: same result, counted once
    assert retry.status_code == 200 and retry.json()["duplicate"]
    assert client.get(f"/v1/vocabulary/diagnostics/{d['id']}").json()["answered"] == 1
    assert db.q1("SELECT COUNT(*) n FROM attempts WHERE kind='word'")["n"] == 1
    other = client.post("/v1/vocabulary/answers", json={**body, "choice": 1})
    assert other.status_code == 409
    assert first.json()["confidence"] == "guess"
    # starting over invalidates the old pending question
    c = client.post(f"/v1/vocabulary/diagnostics/{d['id']}/next").json()["item"]
    new = client.post("/v1/vocabulary/diagnostics", json={"replace": True}).json()
    assert new["id"] != d["id"]
    assert client.post("/v1/vocabulary/answers", json={"item_id": c["item_id"], "choice": 0}).status_code == 409


# --- phase 3: FSRS review ----------------------------------------------------------------------

def test_review_queue_due_cards_and_history(client):
    run_diag(client, lambda sec: sec["modality"] == "text")
    with db.tx() as conn:
        conn.execute("UPDATE srs_cards SET due='2000-01-01T00:00:00+00:00' WHERE kind LIKE 'w_%'")
    due = vocab.due_count()
    assert due > 0
    r = client.get("/v1/vocabulary/reviews?limit=5").json()
    assert r["due"] == due and len(r["items"]) == 5
    assert all(i["review"] for i in r["items"])
    # asking again (another device) returns the same open items, not new duplicates
    r2 = client.get("/v1/vocabulary/reviews?limit=5").json()
    assert [i["item_id"] for i in r2["items"]] == [i["item_id"] for i in r["items"]]
    item = r["items"][0]
    row = db.q1("SELECT payload FROM vocab_items WHERE id=?", (item["item_id"],))
    sec = json.loads(row["payload"])["secret"]
    before = db.q1("SELECT * FROM srs_cards WHERE id=?", (sec["card"],))
    fb = client.post("/v1/vocabulary/reviews", json={"item_id": item["item_id"], "choice": sec["answer"], "ms": 1500}).json()
    assert fb["correct"] and fb["meaning"] and fb["srs"]["rating"] == "Good"
    after = db.q1("SELECT * FROM srs_cards WHERE id=?", (sec["card"],))
    assert after["reps"] == before["reps"] + 1 and after["due"] > before["due"]
    assert after["due"] > db.now_iso(), "a reviewed card leaves the due-now queue"
    # the open review list no longer contains the answered item
    left = client.get("/v1/vocabulary/reviews?limit=5").json()["items"]
    assert item["item_id"] not in [i["item_id"] for i in left]
    row = db.q1("SELECT next_review_at FROM user_vocab WHERE word_id=?", (sec["word_id"],))
    assert row["next_review_at"]


def test_acquired_requires_all_three_modalities(client):
    w = vocab_library().words["allocate"]
    with db.tx() as conn:
        for mod in ("text", "audio"):
            p = vocab.build_item(w, mod)
            vocab.grade(conn, p, {"choice": p["secret"]["answer"], "ms": 1000}, origin="test")
    row = db.q1("SELECT * FROM user_vocab WHERE word_id='allocate'")
    assert vocab.status_of(row) != "acquired"
    with db.tx() as conn:
        p = vocab.build_item(w, "context")
        fb = vocab.grade(conn, p, {"choice": p["secret"]["answer"], "ms": 1000}, origin="test")
    # all three right, but on one day only: confirmed on another day before it counts as acquired
    assert fb["status"] == "confirm"
    with db.tx() as conn:
        conn.execute("UPDATE user_vocab SET last_correct_date='2000-01-01' WHERE word_id='allocate'")
        p = vocab.build_item(w, "text")
        fb = vocab.grade(conn, p, {"choice": p["secret"]["answer"], "ms": 1000}, origin="test")
    assert fb["status"] == "acquired"
    # a lucky guess is not the same as knowing: half credit and a review card
    w2 = vocab_library().words["postpone"]
    with db.tx() as conn:
        p = vocab.build_item(w2, "text")
        fb = vocab.grade(conn, p, {"choice": p["secret"]["answer"], "confidence": "guess"}, origin="test")
    assert fb["score"] == 0.5 and fb["srs"]["rating"] == "Hard"


# --- phase 5: course integration ----------------------------------------------------------------

def test_words_block_in_sessions(client):
    from tests.test_flow import run
    client.post("/onboarding/skip", json={"level": 2})
    for minutes in (60, 90, 120):
        assert "words" in [b["kind"] for b in client.post("/plan/preview", json={"minutes": minutes}).json()["blocks"]]
    # 30 minutes: no separate block; the review block turns to TOEIC words when nothing is due (checked below)
    s = client.post("/sessions", json={"minutes": 30}).json()
    types, done = run(client, s["id"], elapsed=30, good=lambda i: i % 3 != 0)
    assert done["session"]["status"] == "completed"
    assert "word" in types
    words = [r["item_id"] for r in db.q("SELECT item_id FROM attempts WHERE session_id=? AND kind='word'", (s["id"],))]
    assert len(words) == len(set(words)), "a word is not asked twice in one session"


# --- phase 4: generated lessons -------------------------------------------------------------------

def test_local_lesson_script_passes_checks():
    lib = vocab_library()
    targets = [lib.words[w] for w in ("allocate", "postpone", "reimbursement")]
    rng = random.Random(3)
    script = lessons.local_script(targets, rng)
    assert all(c["ok"] for c in lessons.check_script(script, targets))
    text = " ".join(t for _, t in script["lines"])
    qs = lessons.make_questions(targets, text, rng)
    assert all(c["ok"] for c in lessons.check_questions(qs, text))


def test_lesson_checks_reject_bad_scripts():
    lib = vocab_library()
    targets = [lib.words["allocate"], lib.words["postpone"]]
    bad = {"lines": [["A", "We need more time."], ["B", "Should we postpone it?"], ["A", "会議"], ["C", "Ok."]]}
    failed = {c["check"] for c in lessons.check_script(bad, targets) if not c["ok"]}
    assert "指定した単語がすべて含まれる" in failed and "英語のみ" in failed and "話者は A / B" in failed
    text = "We need to allocate more time."
    q = {"type": "vocab-heard", "q": "x", "choices": ["allocate", "postpone", "budget", "merger"], "answer": 0, "word": "allocate"}
    assert lessons.check_questions([q], text)[0]["ok"]
    q2 = dict(q, choices=["allocate", "postpone", "budget", "allocate"])
    assert not lessons.check_questions([q2], text)[0]["ok"]
    assert not lessons.check_questions([q], text + " Let's postpone it.")[0]["ok"]  # two right answers
    res = {"duration": 3.0, "segments": [{"start": 0, "end": 0.1}]}
    assert not all(c["ok"] for c in lessons.check_audio(res, [["A", "This sentence has quite a few words in it."]]))


def test_gemini_response_parsing():
    good = {"candidates": [{"content": {"parts": [{"text": json.dumps(
        {"topic": "Budget", "dialogue": [{"speaker": "A", "text": "We need to allocate more money — now."},
                                         {"speaker": "b", "text": "Should we postpone it?"}]})}]}}]}
    s = lessons.parse_gemini_response(good)
    assert s["lines"] == [["A", "We need to allocate more money , now."], ["B", "Should we postpone it?"]]
    with pytest.raises(lessons.LessonError):
        lessons.parse_gemini_response({"candidates": []})


def test_generate_lesson_end_to_end(client, tmp_path, monkeypatch):
    from app import config, tts
    from app.content import library
    if not tts.available()[0]:
        pytest.skip("TTS not available on this machine")
    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path / "media")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    r = client.post("/v1/audio-lessons/generate", json={"words": ["allocate", "postpone", "reimbursement"]})
    assert r.status_code == 200, r.text
    lesson = r.json()
    assert lesson["engine"] == "local" and not lesson["cached"]
    assert all(c["ok"] for c in lesson["checks"])
    from app import asr
    if asr.available()[0]:   # the synthesized audio was transcribed and matched the script
        names = [c["check"] for c in lesson["checks"] if not c.get("skipped")]
        assert any("音声と英文が一致" in n for n in names) and any("指定語が音声" in n for n in names)
    clip = library().clips[lesson["id"]]
    assert clip.source == "lesson" and clip.audio and clip.targets == ["allocate", "postpone", "reimbursement"]
    assert (tmp_path / "media" / clip.audio).exists()
    # same request again: served from cache, no second lesson
    again = client.post("/v1/audio-lessons/generate", json={"words": ["postpone", "allocate", "reimbursement"]}).json()
    assert again["id"] == lesson["id"] and again["cached"]
    assert len(client.get("/v1/audio-lessons").json()["lessons"]) == 1
    # lessons come first as new listening material, and dictating a target word is context evidence
    client.post("/onboarding/skip", json={"level": clip.level})
    s = client.post("/sessions", json={"minutes": 15}).json()
    while True:
        r = client.post(f"/sessions/{s['id']}/next", json={"elapsed": 1}).json()
        item = r["item"]
        if item["type"] == "comprehension":
            assert item["clip"]["id"] == lesson["id"]
            client.post(f"/sessions/{s['id']}/answer", json={"item_id": item["item_id"], "answers": [0, 0, 0]})
            break
        client.post(f"/sessions/{s['id']}/answer", json={"item_id": item["item_id"], "choice": 0, "text": "x", "rating": 2})
    seg = next(sg for sg in clip.segments if "allocate" in sg.text)
    while True:
        r = client.post(f"/sessions/{s["id"]}/next", json={"elapsed": 1}).json()
        assert "item" in r, (r, [x.text for x in clip.segments])
        item = r["item"]
        text = seg.text if item["type"] == "dictation" and item["segment"]["id"] == seg.id else "x"
        client.post(f"/sessions/{s['id']}/answer", json={"item_id": item["item_id"], "text": text})
        if text != "x":
            break
    row = db.q1("SELECT * FROM user_vocab WHERE word_id='allocate'")
    assert row["context_n"] == 1 and row["context_mastery"] == 1.0
    assert client.delete(f"/v1/audio-lessons/{lesson['id']}").status_code == 200
    assert lesson["id"] not in library().clips
    assert not (tmp_path / "media" / clip.audio).exists()


# --- Gemini integration (network is always mocked) ------------------------------------------

FAKE_KEY = "AIzaTEST_" + "x" * 30


class _FakeResp:
    def __init__(self, data):
        self._b = json.dumps(data).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gemini_reply(lines):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(
        {"topic": "Budget review", "dialogue": [{"speaker": sp, "text": t} for sp, t in lines]})}]}}]}


GOOD_DIALOGUE = [
    ("A", "We need to allocate more money to the training program this year."),
    ("B", "I agree, but the board asked us to postpone new spending until June."),
    ("A", "Then let's at least process the reimbursement requests from last month."),
    ("B", "Good idea. I'll send the forms to accounting this afternoon."),
    ("A", "Thanks. Can we meet again on Friday to review the numbers?"),
    ("B", "Sure, I'll book the small meeting room for ten o'clock."),
]


def test_gemini_key_is_write_only(client, tmp_path):
    from app import lessons
    st = client.get("/v1/integrations/gemini").json()
    assert not st["gemini_configured"] and st["engine"] == "local" and st["mode"] == "auto"
    r = client.put("/v1/integrations/gemini", json={"api_key": "short"})
    assert r.status_code == 400 and "短すぎます" in r.json()["detail"]
    r = client.put("/v1/integrations/gemini", json={"api_key": "bad key with spaces " * 2})
    assert r.status_code == 400 and "空白" in r.json()["detail"]
    r = client.put("/v1/integrations/gemini", json={"api_key": "ＡＢＣ" + "é" * 30})
    assert r.status_code == 400 and "全角" in r.json()["detail"]
    # what people paste: quotes, env lines, full-width letters, zero-width spaces, dotted key formats
    from app import lessons
    for pasted in (f'"{FAKE_KEY}"', f"GEMINI_API_KEY={FAKE_KEY}", f"export GEMINI_API_KEY='{FAKE_KEY}'",
                   "\u200b" + FAKE_KEY + "\ufeff", FAKE_KEY.replace("A", "Ａ", 1)):
        assert lessons.normalize_key(pasted) == FAKE_KEY, pasted
    assert lessons.key_problem(lessons.normalize_key("AQ.Ab8RN6" + "x" * 40)) is None
    r = client.put("/v1/integrations/gemini", json={"api_key": f"  {FAKE_KEY}\n"})
    assert r.status_code == 200
    st = r.json()
    assert st["gemini_configured"] and st["key_source"] == "file" and st["engine"] == "gemini"
    assert st["key_hint"] == "…" + FAKE_KEY[-4:]
    f = lessons.key_file()
    assert f.read_text() == FAKE_KEY and (f.stat().st_mode & 0o777) == 0o600
    # the key never comes back over HTTP
    for path in ("/v1/integrations/gemini", "/v1/vocabulary/stats", "/v1/audio-lessons", "/profile", "/export"):
        assert FAKE_KEY not in client.get(path).text, path
    # engine mode and model are settings
    s = client.put("/settings", json={"lesson_engine": "local", "gemini_model": "gemini-flash-lite-latest"}).json()
    assert s["lesson_engine"] == "local" and s["gemini_model"] == "gemini-flash-lite-latest"
    assert client.put("/settings", json={"gemini_model": "../../etc"}).status_code == 422
    st = client.get("/v1/integrations/gemini").json()
    assert st["engine"] == "local" and st["gemini_model"] == "gemini-flash-lite-latest"
    assert not client.delete("/v1/integrations/gemini").json()["gemini_configured"]
    assert not f.exists()


def test_gemini_connection_test(client, monkeypatch):
    import urllib.error

    from app import lessons
    assert client.post("/v1/integrations/gemini/test").status_code == 400  # no key yet
    client.put("/v1/integrations/gemini", json={"api_key": FAKE_KEY})
    seen = []

    def ok(req, timeout=0):
        seen.append((req.full_url, req.get_header("X-goog-api-key"), req.data))
        if req.data:   # the tiny generation that proves the model is actually served to this key
            return _FakeResp({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
        return _FakeResp({"name": "models/gemini-flash-latest", "displayName": "Gemini Flash Latest",
                          "supportedGenerationMethods": ["generateContent"]})
    monkeypatch.setattr(lessons.urllib.request, "urlopen", ok)
    r = client.post("/v1/integrations/gemini/test").json()
    assert r["ok"] and r["display_name"] == "Gemini Flash Latest"
    assert seen[0][0].endswith("/models/gemini-flash-latest") and seen[1][0].endswith(":generateContent")
    assert all(k == FAKE_KEY and FAKE_KEY not in u for u, k, _ in seen)   # key in the header, not the URL
    assert json.loads(seen[1][2])["generationConfig"]["maxOutputTokens"] <= 16

    def retired(req, timeout=0):   # what Google answered for gemini-2.5-flash with a new key (Nov 2026)
        if not req.data:
            return _FakeResp({"name": "models/x", "supportedGenerationMethods": ["generateContent"]})
        import io
        body = io.BytesIO(json.dumps({"error": {"message": "This model is no longer available to new users. key AIzaLEAK1234567890123456"}}).encode())
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, body)
    monkeypatch.setattr(lessons.urllib.request, "urlopen", retired)
    r = client.post("/v1/integrations/gemini/test")
    assert r.status_code == 502 and "no longer available" in r.json()["detail"] and "AIzaLEAK" not in r.text

    def denied(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(lessons.urllib.request, "urlopen", denied)
    r = client.post("/v1/integrations/gemini/test")
    assert r.status_code == 502 and "403" in r.json()["detail"] and FAKE_KEY not in r.text


def _need_tts(tmp_path, monkeypatch):
    from app import config, tts
    if not tts.available()[0]:
        pytest.skip("TTS not available on this machine")
    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path / "media")


def test_gemini_lesson_generation(client, tmp_path, monkeypatch):
    from app import lessons
    _need_tts(tmp_path, monkeypatch)
    client.put("/v1/integrations/gemini", json={"api_key": FAKE_KEY})
    calls = []

    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        calls.append(body)
        assert req.full_url.endswith("/models/gemini-flash-latest:generateContent")
        return _FakeResp(_gemini_reply(GOOD_DIALOGUE))
    monkeypatch.setattr(lessons.urllib.request, "urlopen", fake)
    r = client.post("/v1/audio-lessons/generate", json={"words": ["allocate", "postpone", "reimbursement"]})
    assert r.status_code == 200, r.text
    lesson = r.json()
    assert lesson["engine"] == "gemini" and not lesson["fallback"] and all(c["ok"] for c in lesson["checks"])
    assert len(calls) == 1
    prompt = calls[0]["contents"][0]["parts"][0]["text"]
    assert "allocate" in prompt and "postpone" in prompt  # only words and a level are sent
    raw = json.loads((lessons.lessons_dir() / f"{lesson['id']}.json").read_text())
    assert raw["engine"] == "gemini" and raw["lines"][0][1].startswith("We need to allocate")


def test_gemini_failure_falls_back_only_in_auto(client, tmp_path, monkeypatch):
    from app import lessons
    _need_tts(tmp_path, monkeypatch)
    client.put("/v1/integrations/gemini", json={"api_key": FAKE_KEY})
    calls = []

    def missing_word(req, timeout=0):  # Gemini forgets "reimbursement" every time
        calls.append(1)
        return _FakeResp(_gemini_reply([(sp, t.replace("reimbursement", "expense")) for sp, t in GOOD_DIALOGUE]))
    monkeypatch.setattr(lessons.urllib.request, "urlopen", missing_word)
    words = {"words": ["allocate", "postpone", "reimbursement"]}
    # explicit Gemini: one retry, then the rejection and its failed checks are reported
    r = client.post("/v1/audio-lessons/generate", json={**words, "engine": "gemini"})
    assert r.status_code == 422 and len(calls) == 2
    assert any(not c["ok"] for c in r.json()["checks"])
    assert not client.get("/v1/audio-lessons").json()["lessons"]
    # auto: the learner still gets a (local) lesson, and the fallback is recorded
    calls.clear()
    r = client.post("/v1/audio-lessons/generate", json=words)
    assert r.status_code == 200, r.text
    lesson = r.json()
    assert len(calls) == 2 and lesson["engine"] == "local" and lesson["fallback"]
    assert lesson["checks"][0]["check"] == "生成エンジン" and all(c["ok"] for c in lesson["checks"])


# --- speech-recognition checks ----------------------------------------------------------------

def test_asr_text_helpers():
    from app import asr
    assert asr.tokens("It's 5 o'clock — well-known “budget”.") == ["it's", "five", "o'clock", "well", "known", "budget"]
    assert asr.wer(["a", "b", "c", "d"], ["a", "b", "c", "d"]) == 0
    assert asr.wer(["a", "b", "c", "d"], ["a", "x", "c"]) == 0.5   # one substitution + one deletion


def _asr_result(text, prob=0.95):
    return {"text": text, "words": [{"word": w, "probability": prob} for w in text.split()], "segments": []}


def test_asr_checks_reject_mismatch_missing_word_and_unclear_speech():
    from app import lessons
    lib = vocab_library()
    targets = [lib.words["allocate"], lib.words["postpone"]]
    lines = [["A", "We need to allocate more money."], ["B", "Should we postpone the launch?"]]
    good = lessons.asr_checks(_asr_result("we need to allocate more money should we postpone the launch"), lines, targets)
    assert all(c["ok"] for c in good)
    # audio says something else
    other = lessons.asr_checks(_asr_result("the train to boston leaves at nine from platform two"), lines, targets)
    assert not other[0]["ok"] and not other[1]["ok"]
    # one target word not heard (small overall error, but the word itself is missing)
    miss = lessons.asr_checks(_asr_result("we need to locate more money should we postpone the launch"), lines, targets)
    assert miss[0]["ok"] and not miss[1]["ok"] and "allocate" in miss[1]["detail"]
    # recognised, but with low confidence -> unclear pronunciation
    unclear = lessons.asr_checks(_asr_result("we need to allocate more money should we postpone the launch", prob=0.4), lines, targets)
    assert not unclear[2]["ok"]


def test_asr_unavailable_is_reported_as_skipped_not_passed(monkeypatch):
    from app import asr, lessons
    monkeypatch.setenv("LISTENFLOW_ASR", "0")
    assert not asr.available()[0]
    c = lessons.check_asr({"audio": "x.mp3"}, [["A", "Hi there."]], [])
    assert len(c) == 1 and c[0].get("skipped") and c[0]["detail"].startswith("未実施")


def test_asr_rejects_audio_that_does_not_match_script():
    """Real faster-whisper on a bundled clip: its own script passes, another clip's script and reversed audio fail."""
    import subprocess
    import tempfile
    from pathlib import Path

    from app import asr, config, lessons
    from app.content import reload_library
    if not asr.available()[0]:
        pytest.skip("faster-whisper not installed")
    lib = reload_library()   # earlier tests point AUDIO_DIR at a temp dir; read the bundled manifest again
    a, b = lib.clips["tA-5"], lib.clips["tB-2"]
    src = config.AUDIO_DIR / a.audio
    with tempfile.TemporaryDirectory() as td:
        rev = Path(td) / "rev.mp3"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", "areverse", str(rev)], check=True)
        same, backwards = asr.transcribe([src, rev])
    own = [["A", s.text] for s in a.segments]
    assert all(c["ok"] for c in lessons.asr_checks(same, own, []))
    assert not lessons.asr_checks(same, [["A", s.text] for s in b.segments], [])[0]["ok"]
    assert not all(c["ok"] for c in lessons.asr_checks(backwards, own, []))


def _fake_tts_and_text(calls, tts_fail=False, wav=False):
    """urlopen stand-in: text requests get GOOD_DIALOGUE, TTS requests get real speech (macOS say, 24 kHz PCM)."""
    import base64
    import subprocess
    import tempfile
    import urllib.error
    import wave
    from pathlib import Path

    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        if "responseModalities" not in body.get("generationConfig", {}):
            calls.append("text")
            return _FakeResp(_gemini_reply(GOOD_DIALOGUE))
        calls.append(body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"])
        if tts_fail:
            raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, None)
        with tempfile.TemporaryDirectory() as td:
            w = Path(td) / "a.wav"
            # the app's level-2 speaking rate (config.LEVEL_RATE), i.e. what a lesson normally sounds like
            subprocess.run(["say", "-v", "Samantha", "-r", "165", "-o", str(w), "--data-format=LEI16@24000",
                            body["contents"][0]["parts"][0]["text"]], check=True)
            with wave.open(str(w)) as f:
                pcm = f.readframes(f.getnframes())
        if wav:   # 3.8 TTS answers with a WAV container instead of raw PCM
            return _FakeResp({"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(wav_bytes(pcm)).decode()}}]}}]})
        return _FakeResp({"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "audio/L16;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}]})

    def wav_bytes(pcm):
        import io
        buf = io.BytesIO()
        with wave.open(buf, "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000); f.writeframes(pcm)
        return buf.getvalue()
    return fake


@pytest.mark.parametrize("wav", [False, True], ids=["pcm", "wav"])
def test_gemini_tts_voices_lesson(client, tmp_path, monkeypatch, wav):
    from app import asr, lessons, tts
    _need_tts(tmp_path, monkeypatch)
    client.put("/v1/integrations/gemini", json={"api_key": FAKE_KEY})
    client.put("/settings", json={"lesson_voice": "gemini"})
    assert client.get("/v1/integrations/gemini").json()["voice"] == "gemini"
    calls = []
    monkeypatch.setattr(lessons.urllib.request, "urlopen", _fake_tts_and_text(calls, wav=wav))
    r = client.post("/v1/audio-lessons/generate", json={"words": ["allocate", "postpone", "reimbursement"]})
    assert r.status_code == 200, r.text
    lesson = r.json()
    raw = json.loads((lessons.lessons_dir() / f"{lesson['id']}.json").read_text())
    assert raw["voice_engine"] == "gemini", lesson["checks"]
    assert set(raw["voices"].values()) <= set(lessons.GEMINI_VOICES)
    assert len(set(raw["voices"].values())) == 2
    assert set(raw["voices"].values()) == {"Algieba", "Aoede"}   # default male + female pair
    assert calls.count("text") == 1 and len(calls) == 1 + len(GOOD_DIALOGUE)   # one TTS call per line
    assert all(c["ok"] for c in lesson["checks"])
    if asr.available()[0]:
        assert any("音声と英文が一致" in c["check"] for c in lesson["checks"])
    # the bulk audio builder never tries to re-create Gemini-voiced lessons with `say`
    built = []
    monkeypatch.setattr(tts, "synthesize_clip", lambda raw, speak=None: built.append(raw["id"]) or {})
    tts.build_all([raw], force=True, log=lambda *_: None)
    assert built == []


def test_gemini_voice_pair_is_configurable(client, tmp_path, monkeypatch):
    from app import lessons
    _need_tts(tmp_path, monkeypatch)
    client.put("/v1/integrations/gemini", json={"api_key": FAKE_KEY})
    st = client.get("/v1/integrations/gemini").json()
    assert (st["gemini_voice_male"], st["gemini_voice_female"]) == ("Algieba", "Aoede")
    assert client.put("/settings", json={"gemini_voice_male": "Nobody"}).status_code == 422
    r = client.put("/settings", json={"lesson_voice": "gemini", "gemini_voice_male": "Charon", "gemini_voice_female": "Kore"})
    assert r.status_code == 200
    calls = []
    monkeypatch.setattr(lessons.urllib.request, "urlopen", _fake_tts_and_text(calls))
    lesson = client.post("/v1/audio-lessons/generate", json={"words": ["allocate", "postpone", "reimbursement"]}).json()
    raw = json.loads((lessons.lessons_dir() / f"{lesson['id']}.json").read_text())
    assert set(raw["voices"].values()) == {"Charon", "Kore"}


def test_gemini_tts_failure_falls_back_to_mac_voices_in_auto(client, tmp_path, monkeypatch):
    from app import lessons
    _need_tts(tmp_path, monkeypatch)
    client.put("/v1/integrations/gemini", json={"api_key": FAKE_KEY})
    client.put("/settings", json={"lesson_voice": "gemini"})
    calls = []
    monkeypatch.setattr(lessons.urllib.request, "urlopen", _fake_tts_and_text(calls, tts_fail=True))
    words = {"words": ["allocate", "postpone", "reimbursement"]}
    lesson = client.post("/v1/audio-lessons/generate", json=words).json()
    raw = json.loads((lessons.lessons_dir() / f"{lesson['id']}.json").read_text())
    assert raw["voice_engine"] == "mac" and lesson["checks"][0]["check"] == "音声エンジン"
    # explicit "Gemini only": no silent fallback
    client.delete(f"/v1/audio-lessons/{lesson['id']}")
    client.put("/settings", json={"lesson_engine": "gemini"})
    r = client.post("/v1/audio-lessons/generate", json=words)
    assert r.status_code == 422 and any("Gemini TTS" in c["check"] for c in r.json()["checks"])


GOOD_JA = ["今年は研修プログラムにもっと予算を割り当てる必要があります。", "賛成ですが、役員会から新しい支出は6月まで延期するよう言われました。",
           "それなら、せめて先月分の経費精算の申請は処理しましょう。", "いいですね。今日の午後、経理に書類を送ります。",
           "ありがとう。金曜日にまた集まって数字を確認できますか？", "もちろん。10時に小会議室を予約します。"]
GOOD_TRANSFERS = [
    {"word": "allocate", "text": "The city will allocate new funds to repair the old bridge.", "ja": "市は古い橋の修理に新たな資金を割り当てる。",
     "q": "この文の内容に合うのは？", "choices": ["橋の修理にお金を回す", "橋を閉鎖する", "新しい橋を売る", "修理を中止する"], "answer": 0},
    {"word": "postpone", "text": "Because of the storm, they postponed the outdoor concert.", "ja": "嵐のため、野外コンサートは延期された。",
     "q": "コンサートはどうなった？", "choices": ["予定どおり開かれた", "屋内に移った", "延期された", "無料になった"], "answer": 2},
    {"word": "reimbursement", "text": "Keep your taxi receipts if you want a reimbursement.", "ja": "払い戻しを受けたいならタクシーの領収書を保管して。",
     "q": "領収書を保管する理由は？", "choices": ["税金を払うため", "お金を返してもらうため", "記念に残すため", "タクシーを予約するため"], "answer": 1},
]


def _notes_fake(calls, verdict):
    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        prompt = body["contents"][0]["parts"][0]["text"]
        if "You are checking teaching material" in prompt:
            calls.append("verify")
            if verdict is None:
                raise OSError("network down")
            return _FakeResp({"candidates": [{"content": {"parts": [{"text": json.dumps(verdict)}]}}]})
        calls.append("text")
        return _FakeResp({"candidates": [{"content": {"parts": [{"text": json.dumps({
            "topic": "Budget review", "scene_ja": "上司と部下が予算と経費について話している。",
            "dialogue": [{"speaker": sp, "text": t, "ja": j} for (sp, t), j in zip(GOOD_DIALOGUE, GOOD_JA)],
            "transfers": GOOD_TRANSFERS})}]}}]})
    return fake


def test_gemini_lesson_carries_verified_translations_and_transfers(client, tmp_path, monkeypatch):
    from app import lessons, study
    _need_tts(tmp_path, monkeypatch)
    client.put("/v1/integrations/gemini", json={"api_key": FAKE_KEY})
    calls = []
    # the checker rejects the second line's translation and the third transfer question
    verdict = {"lines": [True, False, True, True, True, True], "transfers": [True, True, False], "issues": ["line 2 adds meaning"]}
    monkeypatch.setattr(lessons.urllib.request, "urlopen", _notes_fake(calls, verdict))
    r = client.post("/v1/audio-lessons/generate", json={"words": ["allocate", "postpone", "reimbursement"]})
    assert r.status_code == 200, r.text
    lesson = r.json()
    assert calls == ["text", "verify"]
    raw = json.loads((lessons.lessons_dir() / f"{lesson['id']}.json").read_text())
    notes = raw["notes"]
    assert notes["scene"] and notes["lines"][1] is None and notes["lines"][0]["ja"] == GOOD_JA[0]
    assert notes["lines"][0]["key"]["expr"] == "allocate" and notes["lines"][0]["transfer"]["form"] == "allocate"
    assert notes["lines"][2]["transfer"] is None                    # rejected by the checker
    check = next(c for c in lesson["checks"] if c["check"] == "訳・応用問題の意味の一致")
    assert "日本語訳 5/6" in check["detail"] and "応用文 2/3" in check["detail"]
    # the lesson's transfer sentence is built (macOS voice) and served like the built-in ones
    t = study.bank().transfers[f"{lesson['id']}#0"]
    assert study.bank().audio(t)
    assert t.voice not in raw["voices"].values()


def test_gemini_notes_dropped_when_unverifiable(client, tmp_path, monkeypatch):
    from app import lessons
    _need_tts(tmp_path, monkeypatch)
    client.put("/v1/integrations/gemini", json={"api_key": FAKE_KEY})
    calls = []
    monkeypatch.setattr(lessons.urllib.request, "urlopen", _notes_fake(calls, None))
    r = client.post("/v1/audio-lessons/generate", json={"words": ["allocate", "postpone", "reimbursement"]})
    assert r.status_code == 200, r.text
    raw = json.loads((lessons.lessons_dir() / f"{r.json()['id']}.json").read_text())
    assert "notes" not in raw                                       # nothing unverified is shown to the learner
    assert any("検証できなかった" in c["detail"] for c in r.json()["checks"])
