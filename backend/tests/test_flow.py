"""End-to-end: diagnostic -> daily session -> review -> analytics, through the HTTP API."""
from app import db
from app.content import library


def solve(item, good=True):
    """Produce an answer for an item; good=False produces a partly wrong one."""
    lib = library()
    t = item["type"]
    if t == "comprehension":
        clip = lib.clips[item["clip"]["id"]]
        return {"answers": [q["answer"] if good else (q["answer"] + 1) % 4 for q in clip.questions]}
    if t == "dictation":
        text = lib.segments[item["segment"]["id"]].text
        return {"text": text if good else " ".join(text.split()[: max(1, len(text.split()) // 2)])}
    if t == "discrimination":
        seg = lib.segments[item["segment"]["id"]]
        return {"choice": item["options"].index(seg.text) if good else 1 - item["options"].index(seg.text)}
    if t == "vocab":
        v = next(v for v in lib.vocab.values() if v["segment_id"] == item["segment"]["id"])
        return {"text": v["word"] if good else "zzz"}
    if t == "shadowing":
        return {"rating": 3 if good else 1}
    if t == "word":
        import json
        sec = json.loads(db.q1("SELECT payload FROM pending_items WHERE id=?", (item["item_id"],))["payload"])["secret"]
        return {"choice": sec["answer"] if good else (sec["answer"] + 1) % 4, "ms": 2000}
    raise AssertionError(t)


def run(client, sid, elapsed=40, good=lambda i: True, max_steps=400):
    seen_types, steps = [], 0
    while steps < max_steps:
        r = client.post(f"/sessions/{sid}/next", json={"elapsed": elapsed}).json()
        if r["type"] == "done":
            return seen_types, r
        if r["type"] == "break":
            client.post(f"/sessions/{sid}/heartbeat", json={"elapsed": 120})
            seen_types.append("break")
            continue
        item = r["item"]
        seen_types.append(item["type"])
        body = {"item_id": item["item_id"], "elapsed": elapsed, **solve(item, good(steps))}
        fb = client.post(f"/sessions/{sid}/answer", json=body)
        assert fb.status_code == 200, fb.text
        steps += 1
    raise AssertionError("session did not finish")


def test_full_learning_loop(client):
    prof = client.get("/profile").json()
    assert prof["onboarded"] is False
    assert not client.get("/health").json()["missing_audio"]

    # 1. diagnostic test (form A) — mostly right at low levels, wrong at high levels
    s = client.post("/sessions", json={"mode": "test", "form": "A"}).json()
    lib = library()
    def good_low(i):
        return True
    types, done = run(client, s["id"], good=good_low)
    assert types.count("comprehension") == 5 and types.count("dictation") == 10
    summ = client.get(f"/sessions/{s['id']}/summary").json()
    assert summ["test"]["form"] == "A" and summ["test"]["score"] == 100.0
    prof = client.get("/profile").json()
    assert prof["onboarded"] and prof["level"] == 5

    # set a mid level to exercise the adaptive selection
    client.post("/onboarding/skip", json={"level": 2})

    # 2. a 30-minute session with some mistakes
    s = client.post("/sessions", json={"minutes": 30}).json()
    assert [b["kind"] for b in s["blocks"]] == ["review", "new", "weak", "words", "shadow"]
    types, done = run(client, s["id"], elapsed=30, good=lambda i: i % 3 != 0)
    assert done["session"]["status"] == "completed"
    assert "comprehension" in types and "dictation" in types and "shadowing" in types and "word" in types
    summ = client.get(f"/sessions/{s['id']}/summary").json()
    assert summ["errors"], "mistakes should be analysed"
    assert summ["new_clips"] >= 1

    # no item repeats inside a session (except a final review of missed lines)
    seg_ids = [r["item_id"] for r in db.q("SELECT item_id FROM attempts WHERE session_id=? AND kind='dictation'", (s["id"],))]
    assert len(seg_ids) == len(set(seg_ids))

    # 3. missed lines become FSRS cards; make them due and run a review session
    cards = db.q("SELECT * FROM srs_cards")
    assert cards
    with db.tx() as conn:
        conn.execute("UPDATE srs_cards SET due='2000-01-01T00:00:00+00:00'")
    assert client.get("/profile").json()["review_due"] == len(cards)
    s = client.post("/sessions", json={"minutes": 10, "mode": "review"}).json()
    r = client.post(f"/sessions/{s['id']}/next", json={}).json()
    assert r["item"].get("review") is True

    # resuming from another device returns the same pending item
    r2 = client.post(f"/sessions/{s['id']}/next", json={}).json()
    assert r2["item"]["item_id"] == r["item"]["item_id"]
    body = {"item_id": r["item"]["item_id"], **solve(r["item"])}
    assert client.post(f"/sessions/{s['id']}/answer", json=body).status_code == 200
    assert client.post(f"/sessions/{s['id']}/answer", json=body).status_code == 409  # double submit

    # 4. pause / extend / finish
    client.post(f"/sessions/{s['id']}/heartbeat", json={"elapsed": 10, "status": "paused"})
    assert client.get("/sessions/active").json()["session"]["status"] == "paused"
    ext = client.post(f"/sessions/{s['id']}/extend", json={"minutes": 15}).json()
    assert ext["minutes"] == 25 and len(ext["blocks"]) > 1
    client.post(f"/sessions/{s['id']}/finish")
    assert client.get("/sessions/active").json()["session"] is None

    # 5. analytics
    a = client.get("/analytics").json()
    assert a["total_minutes"] > 0 and a["items_done"] > 0
    assert any(m["evidence"] > 0 for m in a["mastery"])
    assert len(a["tests"]) == 1


def test_120_minute_session_completes_with_breaks_and_variety(client):
    client.post("/onboarding/skip", json={"level": 3})
    client.put("/settings", json={"break_enabled": True, "break_every": 45, "break_minutes": 5})
    s = client.post("/sessions", json={"minutes": 120}).json()
    types, done = run(client, s["id"], elapsed=60, good=lambda i: i % 4 != 0, max_steps=600)
    assert done["session"]["status"] == "completed"
    assert "break" in types
    for t in ("comprehension", "dictation", "vocab", "shadowing", "word"):
        assert t in types, t
    clips = [r["item_id"] for r in db.q("SELECT item_id FROM attempts WHERE session_id=? AND kind='comprehension'", (s["id"],))]
    assert len(clips) == len(set(clips)), "the same audio must not be served twice as new material"


def test_one_active_session_at_a_time(client):
    client.post("/onboarding/skip", json={"level": 1})
    a = client.post("/sessions", json={"minutes": 15}).json()
    assert client.post("/sessions", json={"minutes": 15}).status_code == 409
    b = client.post("/sessions", json={"minutes": 15, "replace": True}).json()
    assert b["id"] != a["id"]
    assert client.get(f"/sessions/{a['id']}").json()["status"] == "abandoned"


def test_level_unlocks_by_performance(client):
    client.post("/onboarding/skip", json={"level": 1})
    s = client.post("/sessions", json={"minutes": 60}).json()
    run(client, s["id"], elapsed=20, good=lambda i: True, max_steps=600)
    assert client.get("/profile").json()["level"] >= 2


def test_audio_served_and_path_traversal_blocked(client):
    seg = next(iter(library().segments.values()))
    assert client.get(f"/audio/{seg.audio}").status_code == 200
    assert client.get("/audio/../../app/main.py").status_code == 404


def test_add_and_delete_user_material(client, tmp_path, monkeypatch):
    from app import config, tts
    if not tts.available()[0]:
        import pytest
        pytest.skip("TTS not available on this machine")
    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path / "media")
    body = {"title": "Test", "level": 2, "voices": {"A": "Samantha"},
            "lines": [{"speaker": "A", "text": "Please send me the report by Friday."}],
            "vocab": [["report", "報告書"], ["missing", "x"]]}
    r = client.post("/library", json=body)
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    clip = library().clips[cid]
    assert clip.audio and (tmp_path / "media" / clip.audio).exists()
    assert [v["word"] for v in clip.vocab] == ["report"]  # words not in the script are dropped
    assert client.delete(f"/library/{cid}").status_code == 200
    assert cid not in library().clips
    assert not (tmp_path / "media" / clip.audio).exists()
    assert client.delete("/library/l1-cafe").status_code == 404  # built-in material can't be deleted
