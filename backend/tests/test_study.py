"""Context -> Sentence -> Transfer: notes data, the cycle inside a session, records and Gemini-made notes."""
import json

import pytest

from app import db, study
from app.content import library
from tests.test_flow import solve


def _secret(item_id):
    return json.loads(db.q1("SELECT payload FROM pending_items WHERE id=?", (item_id,))["payload"])["secret"]


def test_builtin_notes_are_complete_and_valid():
    b = study.reload_bank()
    assert b.errors == []
    lib = library()
    builtin_train = [c for c in lib.clips.values() if c.source == "builtin" and c.pool == "train"]
    for c in builtin_train:
        assert c.id in b.notes, c.id
        lines = b.notes[c.id]["lines"]
        assert len(lines) == len(c.segments)
        assert all(n and n["ja"] for n in lines), c.id                 # every line has a translation
        assert b.notes[c.id]["scene"]
    assert len(b.transfers) >= 150
    for t in b.transfers.values():
        seg = lib.segments[t.id]
        assert t.text.lower() != seg.text.lower()
        assert t.voice != seg.voice                                     # another speaker than the practised line
    answers = [t.answer for t in b.transfers.values()]
    assert max(answers.count(i) for i in range(4)) < 0.4 * len(answers)   # the key is not guessable by position
    assert b.missing_audio() == []


def test_validation_rejects_broken_transfer():
    good = {"text": "They decided to postpone the product launch.", "form": "postpone", "ja": "x", "q": "q",
            "choices": ["a", "b", "c", "d"], "answer": 2}
    assert study.validate_transfer(good) == []
    assert study.validate_transfer({**good, "form": "delay"})
    assert study.validate_transfer({**good, "choices": ["a", "a", "c", "d"]})
    assert study.validate_transfer({**good, "answer": 4})
    assert study.validate_transfer({**good, "text": "延期 the launch please now"})
    assert study.validate_line("We might postpone it.", {"ja": "延期するかも", "key": {"expr": "delay", "ja": "x"}})


@pytest.mark.parametrize("check,understood,expected", [
    ("clear", True, "phonetic"), ("clear", None, "phonetic"), ("clear", False, "grammar"),
    ("unknown_words", None, "vocabulary"), ("structure", None, "grammar"), (None, None, "unknown")])
def test_classify(check, understood, expected):
    assert study.classify(check, understood) == expected


def _start(client, level=2):
    client.post("/onboarding/skip", json={"level": level})
    s = client.post("/sessions", json={"minutes": 30}).json()
    return s["id"]


def _next(client, sid, elapsed=5):
    r = client.post(f"/sessions/{sid}/next", json={"elapsed": elapsed}).json()
    assert r["type"] == "item", r
    return r["item"]


def _answer(client, sid, item, body, elapsed=5):
    r = client.post(f"/sessions/{sid}/answer", json={"item_id": item["item_id"], "elapsed": elapsed, **body})
    assert r.status_code == 200, r.text
    return r.json()


def _to_conversation(client, sid):
    item = _next(client, sid)
    while item["type"] != "comprehension":          # review warm-up with nothing due is skipped quickly
        _answer(client, sid, item, solve(item))
        item = _next(client, sid)
    return item


def test_cycle_context_sentence_transfer(client):
    sid = _start(client)
    comp = _to_conversation(client, sid)
    cid = comp["clip"]["id"]
    _answer(client, sid, comp, solve(comp, good=False))           # the gist was missed on first listen
    # lines of the same conversation until one is not heard
    d1 = _next(client, sid)
    assert d1["type"] == "dictation" and d1["segment"]["id"].startswith(cid + "#")
    fb = _answer(client, sid, d1, solve(d1, good=False))
    assert fb["accuracy"] < study.STUDY_BELOW and fb["study_next"]
    st = _next(client, sid)
    assert st["type"] == "study" and st["segment"]["id"] == d1["segment"]["id"]
    seg = library().segments[d1["segment"]["id"]]
    assert st["text"] == seg.text and st["ja"] and st["scene"]
    assert st["listening"], "what was mis-heard is explained"
    fb = _answer(client, sid, st, {"text_check": "clear", "meaning_understood": True, "ms": 3000})
    assert fb["error_type"] == "phonetic"
    tr = _next(client, sid)
    if study.bank().transfers.get(seg.id):
        assert fb["transfer_next"] and tr["type"] == "transfer"
        assert "text" not in tr and tr["audio"]                     # heard, not read
        t = study.bank().transfers[seg.id]
        assert t.text.lower() != seg.text.lower()
        fb = _answer(client, sid, tr, solve(tr))
        assert fb["correct"] and fb["form"] in fb["text"] and fb["ja"] and fb["practice"] == seg.text
        nxt = _next(client, sid)
        assert nxt["type"] == "comprehension" and nxt["clip"]["id"] != cid   # next conversation
    rec = db.q1("SELECT * FROM sentence_studies WHERE session_id=?", (sid,))
    assert rec["clip_id"] == cid and rec["first_listen"] == 1 and rec["context_score"] < 1
    assert rec["segment_id"] == seg.id and rec["dictation_accuracy"] < 0.9
    assert rec["text_check"] == "clear" and rec["meaning_understood"] == 1 and rec["error_type"] == "phonetic"
    # first-listen measures are not mixed with anything seen after the explanation
    kinds = [r["kind"] for r in db.q("SELECT kind FROM attempts WHERE session_id=?", (sid,))]
    assert kinds.count("comprehension") == 1
    lp = client.get("/v1/listening-profile").json()
    assert lp["error_trends"]["n"] == 1 and lp["error_trends"]["dominant"] is None   # one study names nothing
    if study.bank().transfers.get(seg.id):
        assert lp["kpis"]["transfer"]["recent"] == {"value": 1.0, "n": 1}


def test_all_lines_heard_but_gist_missed_is_integration(client):
    sid = _start(client)
    comp = _to_conversation(client, sid)
    _answer(client, sid, comp, solve(comp, good=False))
    item = _next(client, sid)
    while item["type"] == "dictation":
        _answer(client, sid, item, solve(item, good=True))
        item = _next(client, sid)
    assert item["type"] == "comprehension"                         # no study step: nothing was mis-heard
    rec = db.q1("SELECT * FROM sentence_studies WHERE session_id=?", (sid,))
    assert rec["error_type"] == "meaning" and rec["segment_id"] is None


def test_block_time_does_not_cut_an_open_cycle(client):
    sid = _start(client)
    comp = _to_conversation(client, sid)
    _answer(client, sid, comp, solve(comp))
    d = _next(client, sid)
    _answer(client, sid, d, solve(d, good=False))
    # the new block's time runs out while the study step is due
    st = client.post(f"/sessions/{sid}/next", json={"elapsed": 120}).json()
    for _ in range(15):
        client.post(f"/sessions/{sid}/heartbeat", json={"elapsed": 120})
    st = client.post(f"/sessions/{sid}/next", json={"elapsed": 120}).json()
    assert st["type"] == "item" and st["item"]["type"] == "study"


def test_wrong_transfer_brings_the_practised_line_back(client):
    sid = _start(client)
    lib = library()
    for _ in range(4):
        comp = _to_conversation(client, sid)
        _answer(client, sid, comp, solve(comp))
        d = _next(client, sid)
        _answer(client, sid, d, solve(d, good=False))
        st = _next(client, sid)
        fb = _answer(client, sid, st, {"text_check": "unknown_words", "unknown_words": ["postpone"]})
        assert fb["error_type"] == "vocabulary"
        if fb.get("transfer_next"):
            tr = _next(client, sid)
            sec = _secret(tr["item_id"])
            fb = _answer(client, sid, tr, {"choice": (sec["answer"] + 1) % 4})
            assert fb["correct"] is False
            card = db.q1("SELECT * FROM srs_cards WHERE id=?", (f"seg:{d['segment']['id']}",))
            assert card is not None
            rec = db.q1("SELECT * FROM sentence_studies WHERE segment_id=?", (d["segment"]["id"],))
            assert rec["transfer_correct"] == 0 and json.loads(rec["unknown_words"]) == ["postpone"]
            # a heard transfer sentence is never offered again (it would test memory)
            assert study.transfer_for(db.connect(), lib.segments[d["segment"]["id"]]) is None
            return
    pytest.fail("no transfer item in four cycles")


# --- commute mode: the same cycle, answered by tapping ------------------------------------------

def _start_commute(client, minutes=10):
    client.post("/onboarding/skip", json={"level": 2})
    r = client.post("/sessions", json={"minutes": minutes, "mode": "commute"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert [b["kind"] for b in s["blocks"]] == ["commute"] and s["mode"] == "commute"
    return s["id"]


@pytest.mark.parametrize("minutes", [5, 10, 15, 30])
def test_commute_plan_is_one_tap_only_block(client, minutes):
    sid = _start_commute(client, minutes)
    s = client.get(f"/sessions/{sid}").json()
    assert s["blocks"][0]["minutes"] == minutes


def test_commute_cycle_needs_no_typing(client):
    sid = _start_commute(client)
    comp = _next(client, sid)
    assert comp["type"] == "comprehension"
    cid = comp["clip"]["id"]
    _answer(client, sid, comp, solve(comp, good=False))
    lc = _next(client, sid)
    assert lc["type"] == "line_check" and lc["segment"]["id"].startswith(cid + "#")
    assert "text" not in lc and lc["index"] == 1 and lc["total"] >= 1          # heard, not read
    fb = _answer(client, sid, lc, {"report": "understood", "plays": 1})
    assert fb["heard"] is True and fb["report"] == "understood" and fb["reference"]
    lc2 = _next(client, sid)
    assert lc2["type"] == "line_check", "every conversation has two lines or more"
    if lc2["type"] == "line_check":
        fb = _answer(client, sid, lc2, {"report": "sound_only", "plays": 2})
        assert fb["study_next"]
        st = _next(client, sid)
        assert st["type"] == "study" and st["segment"]["id"] == lc2["segment"]["id"] and st["accuracy"] is None
        assert st["report"] == "sound_only"
        fb = _answer(client, sid, st, {"text_check": "clear", "meaning_understood": True})
        assert fb["error_type"] == "phonetic"
        if fb.get("transfer_next"):
            tr = _next(client, sid)
            assert tr["type"] == "transfer"
            _answer(client, sid, tr, solve(tr))
        # the missed line comes back later as a normal review card
        assert db.q1("SELECT 1 FROM srs_cards WHERE id=?", (f"seg:{lc2['segment']['id']}",))
        rec = db.q1("SELECT * FROM sentence_studies WHERE session_id=?", (sid,))
        assert rec["mode"] == "commute" and rec["segment_id"] == lc2["segment"]["id"] and rec["dictation_accuracy"] is None
        assert rec["line_report"] == "sound_only"            # heard the words, not the meaning
    # taps are self-reports: no dictation attempt, the skill estimates do not move
    kinds = [r["kind"] for r in db.q("SELECT kind FROM attempts WHERE session_id=?", (sid,))]
    assert "dictation" not in kinds and "line_check" in kinds
    blocks = {r["block"] for r in db.q("SELECT block FROM attempts WHERE session_id=? AND kind='comprehension'", (sid,))}
    assert blocks == {"commute"}
    k = client.get("/v1/listening-profile").json()["kpis"]
    assert k["unseen_by_mode"]["commute"]["recent"]["n"] == 1
    assert k["unseen_by_mode"]["standard"]["recent"]["n"] == 0
    assert k["line_heard"]["recent"]["n"] >= 1


def test_commute_moves_on_after_lines_heard(client):
    sid = _start_commute(client)
    comp = _next(client, sid)
    _answer(client, sid, comp, solve(comp))
    seen = 0
    item = _next(client, sid)
    while item["type"] == "line_check":
        seen += 1
        _answer(client, sid, item, {"report": "understood"})
        item = _next(client, sid)
    assert item["type"] == "comprehension" and seen <= 2       # two heard lines in a row -> next conversation


def test_commute_extend_adds_commute_time(client):
    sid = _start_commute(client, 5)
    s = client.post(f"/sessions/{sid}/extend", json={"minutes": 10}).json()
    assert [b["kind"] for b in s["blocks"]] == ["commute", "commute"]


def test_commute_line_reports(client):
    sid = _start_commute(client)
    comp = _next(client, sid)
    _answer(client, sid, comp, solve(comp))
    lc = _next(client, sid)
    assert client.post(f"/sessions/{sid}/answer", json={"item_id": lc["item_id"], "report": "maybe"}).status_code == 422
    fb = _answer(client, sid, lc, {"heard": False})                    # the older two-button page still works
    assert fb["report"] == "not_heard" and fb["study_next"]
    rec = db.q1("SELECT line_report FROM sentence_studies WHERE session_id=?", (sid,))
    assert rec["line_report"] == "not_heard"
    detail = json.loads(db.q1("SELECT detail FROM attempts WHERE session_id=? AND kind='line_check'", (sid,))["detail"])
    assert detail["report"] == "not_heard"
