"""Listening profile: 4-ability diagnostic, adaptive planning, checkpoints, KPIs, recommendations, growth."""
import json

import pytest

from app import abilities, db, planner, sentences
from app.content import library
from tests.test_flow import run, solve


def test_sentence_bank_is_valid_and_single_answer():
    b = sentences.reload_bank()
    assert len(b.items) == 40 and not b.missing_audio()
    assert {lv: len(b.by_level(lv)) for lv in range(1, 6)} == {1: 8, 2: 8, 3: 8, 4: 8, 5: 8}
    for x in b.items.values():
        assert len(set(x.choices)) == 4 and 0 <= x.answer < 4
        it = sentences.item(x)
        assert it["public"]["choices"][it["secret"]["answer"]] == x.choices[x.answer]   # shuffling keeps the answer
        assert x.text not in json.dumps(it["public"])                                    # the sentence is not shown
    bad = {"dataset": {"license": "CC0-1.0"}, "items": [
        {"id": "a", "level": 9, "focus": "x", "text": "Hi", "choices": ["1", "1", "2", "3"], "answer": 7}]}
    errs = sentences.validate(bad)
    assert any("level" in e for e in errs) and any("focus" in e for e in errs)
    assert any("distinct" in e for e in errs) and any("answer" in e for e in errs)


def test_checkpoints_every_14_days_with_equal_structure():
    from app import progress
    from app.content import reload_library
    assert progress.CHECKPOINT_DAYS == (1, 15, 29, 43, 57) and progress.FORMS == ("A", "B", "C", "D", "E")
    lib = reload_library()   # other tests point AUDIO_DIR at temp dirs
    shape = None
    for form in progress.FORMS:
        clips = lib.test_clips(form)
        s = [(c.level, len(c.questions)) for c in clips]
        assert s == [(1, 2), (2, 2), (3, 2), (4, 2), (5, 2)], form
        assert all(c.audio for c in clips)
        shape = shape or s
    ids = [c.id for f in progress.FORMS for c in lib.test_clips(f)]
    assert len(ids) == len(set(ids)) == 25
    assert not {c.id for c in lib.train_clips()} & set(ids)    # test audio never appears in training


def _profile_session(client, sound_good: bool):
    s = client.post("/sessions", json={"mode": "profile", "minutes": 0}).json()
    assert [b["kind"] for b in s["blocks"]] == ["d_sound", "d_words", "d_sentence", "test"]
    seen = []
    lib = library()

    def answer_for(item):
        if item["type"] == "discrimination" and not sound_good:
            seg = lib.segments[item["segment"]["id"]]
            return {"choice": 1 - item["options"].index(seg.text)}
        return solve(item, True)

    while True:
        r = client.post(f"/sessions/{s['id']}/next", json={"elapsed": 20}).json()
        if r["type"] == "done":
            return s["id"], seen
        item = r["item"]
        seen.append((item["block"], item["type"]))
        body = {"item_id": item["item_id"], "elapsed": 20, "plays": 1, **answer_for(item)}
        fb = client.post(f"/sessions/{s['id']}/answer", json=body)
        assert fb.status_code == 200, fb.text
        if item["block"] == "d_words":
            assert fb.json().get("recorded") and "answer" not in fb.json()   # diagnostic never reveals answers
        assert len(seen) < 120


def test_profile_diagnostic_measures_four_abilities_and_adapts_plan(client):
    sid, seen = _profile_session(client, sound_good=False)
    by_block = {}
    for b, t in seen:
        by_block.setdefault(b, []).append(t)
    assert by_block["d_sound"] == ["discrimination"] * 10
    assert by_block["d_words"] == ["word"] * 20
    assert by_block["d_sentence"] == ["sentence"] * 10
    assert by_block["test"].count("comprehension") == 5            # first checkpoint form: 10 conversation questions
    # vocabulary part is a real (20-item) vocabulary diagnostic
    vd = db.q1("SELECT * FROM vocab_diagnostics WHERE status='completed'")
    assert vd and json.loads(vd["result"])["items"] == 20
    prof = client.get("/profile").json()
    assert prof["onboarded"] and prof["listening_profile"]["diagnosed"]
    lp = client.get("/v1/listening-profile").json()
    snap = lp["history"][-1]
    assert snap["source"] == "diagnostic" and snap["session_id"] == sid
    assert snap["data"]["sound"] == {"value": 0.0, "n": 10}
    assert snap["data"]["sentence"]["value"] == 1.0 and snap["data"]["conversation"]["value"] == 1.0
    assert lp["priority"] == "sound" and "音" in lp["advice"]
    # all vocabulary answers were right: correct audio answers count (they also credit text mastery)
    assert snap["data"]["vocab"]["value"] == 1.0 and lp["abilities"]["vocab"]["value"] == 1.0
    from app import progress
    assert db.q1("SELECT form FROM tests")["form"] == progress.form_for_slot(1)   # this learner's first form
    # the next plan moves time to sound training, keeps never-heard listening >= 25%
    plan = client.post("/plan/preview", json={"minutes": 30}).json()["blocks"]
    base = planner.build_plan(30, {}, "standard", None)
    weak = next(b for b in plan if b["kind"] == "weak")
    assert weak["minutes"] > next(b for b in base if b["kind"] == "weak")["minutes"]
    assert "音声識別" in weak["adapted"]
    total = sum(b["minutes"] for b in plan if b["kind"] != "break")
    assert total == pytest.approx(30, abs=0.01)
    assert sum(b["minutes"] for b in plan if b["kind"] == "new") >= 0.25 * total
    # extending a session starts with the weakest ability, not with review
    s = client.post("/sessions", json={"minutes": 30}).json()
    ext = client.post(f"/sessions/{s['id']}/extend", json={"minutes": 30}).json()
    added = [b["kind"] for b in ext["blocks"][len(plan) - 1:]]
    assert "weak" in added and "review" not in [b["kind"] for b in ext["blocks"][len(s["blocks"]) - 1:]]


def test_retaking_profile_diagnostic_uses_unheard_training_clips(client):
    _profile_session(client, sound_good=True)
    heard_before = {r["item_id"] for r in db.q("SELECT item_id FROM exposures WHERE kind='clip'")}
    sid, seen = _profile_session(client, sound_good=True)
    assert db.q1("SELECT COUNT(*) n FROM tests")["n"] == 1          # form A is not reused
    asked = [r["item_id"] for r in db.q("SELECT item_id FROM attempts WHERE session_id=? AND kind='comprehension'", (sid,))]
    assert len(asked) == 5
    lib = library()
    assert all(lib.clips[c].pool == "train" and c not in heard_before for c in asked)
    # and they now count as heard, so they are not offered again as never-heard audio
    heard_after = {r["item_id"] for r in db.q("SELECT item_id FROM exposures WHERE kind='clip'")}
    assert set(asked) <= heard_after
    assert len(client.get("/v1/listening-profile").json()["history"]) == 2


def test_plan_adaptation_rules():
    def prof(**vals):
        return {a: {"value": vals.get(a), "n": 10 if vals.get(a) is not None else 0} for a in abilities.ABILITIES}
    base = planner.build_plan(60, {"break_enabled": False})
    # nothing below target -> unchanged
    assert planner.build_plan(60, {"break_enabled": False}, profile=prof(sound=0.9, vocab=0.9, sentence=0.9, conversation=0.9)) == base
    # too little evidence is ignored
    few = prof(sound=0.2)
    few["sound"]["n"] = 3
    assert planner.build_plan(60, {"break_enabled": False}, profile=few) == base
    # weak sentence processing -> a sentence block appears, total time is kept
    p = planner.build_plan(60, {"break_enabled": False}, profile=prof(sound=0.9, vocab=0.9, sentence=0.4, conversation=0.9))
    sb = [b for b in p if b["kind"] == "sentence"]
    assert sb and sb[0]["minutes"] >= 5 and "構文理解" in sb[0]["adapted"]
    assert sum(b["minutes"] for b in p) == pytest.approx(60, abs=0.01)
    # weak conversation -> more never-heard listening
    p = planner.build_plan(60, {"break_enabled": False}, profile=prof(sound=0.9, vocab=0.9, sentence=0.9, conversation=0.4))
    assert sum(b["minutes"] for b in p if b["kind"] == "new") > sum(b["minutes"] for b in base if b["kind"] == "new")
    # short sessions are not reshuffled
    assert planner.build_plan(15, {}, profile=prof(sound=0.1)) == planner.build_plan(15, {})


def test_sentence_block_and_review_cards(client):
    client.post("/onboarding/skip", json={"level": 2})
    s = client.post("/sessions", json={"minutes": 30}).json()
    with db.tx() as conn:   # force a sentence block to the front to exercise it
        row = conn.execute("SELECT plan, state FROM sessions WHERE id=?", (s["id"],)).fetchone()
        plan = [planner._block("sentence", 3)] + json.loads(row["plan"])
        st = json.loads(row["state"])
        st["block_seconds"].insert(0, 0.0)
        st["block_items"].insert(0, 0)
        conn.execute("UPDATE sessions SET plan=?, state=? WHERE id=?", (json.dumps(plan), json.dumps(st), s["id"]))
    r = client.post(f"/sessions/{s['id']}/next", json={"elapsed": 1}).json()
    item = r["item"]
    assert item["type"] == "sentence" and item["audio"] and "text" not in item
    wrong = (solve(item)["choice"] + 1) % 4
    fb = client.post(f"/sessions/{s['id']}/answer", json={"item_id": item["item_id"], "choice": wrong}).json()
    assert fb["correct"] is False and fb["text"] and fb["srs"]                # transcript only after answering
    sid_ = db.q1("SELECT item_id FROM attempts WHERE kind='sentence'")["item_id"]
    assert db.q1("SELECT 1 FROM srs_cards WHERE id=?", (f"sent:{sid_}",))
    # a later correct answer to the same sentence counts as growth
    from app import engine
    with db.tx() as conn:
        conn.execute("UPDATE pending_items SET answered=2 WHERE session_id=?", (s["id"],))
        x = sentences.bank().items[sid_]
        p = sentences.item(x)
        st = json.loads(conn.execute("SELECT state FROM sessions WHERE id=?", (s["id"],)).fetchone()["state"])
        p["public"]["item_id"], p["public"]["block"] = "manual1", "sentence"
        conn.execute("INSERT INTO pending_items (id, session_id, created_at, payload) VALUES (?,?,?,?)",
                     ("manual1", s["id"], db.now_iso(), json.dumps(p)))
    fb = client.post(f"/sessions/{s['id']}/answer", json={"item_id": "manual1", "choice": p["secret"]["answer"]}).json()
    assert fb["correct"]
    g = client.get("/v1/growth").json()["items"]
    assert g and g[0]["kind"] == "sentence"


def test_kpis_first_listen_and_retention(client):
    client.post("/onboarding/skip", json={"level": 2})
    s = client.post("/sessions", json={"minutes": 15}).json()
    lib = library()
    for _ in range(60):
        item = client.post(f"/sessions/{s['id']}/next", json={"elapsed": 5}).json()["item"]
        body = {"item_id": item["item_id"], **solve(item)}
        if item["type"] == "comprehension":
            body["plays"] = 1
            client.post(f"/sessions/{s['id']}/answer", json=body)
            break
        client.post(f"/sessions/{s['id']}/answer", json=body)
    k = client.get("/v1/listening-profile").json()["kpis"]
    assert k["unseen_comprehension"]["recent"]["n"] == 1 and k["unseen_comprehension"]["recent"]["value"] == 1.0
    assert k["first_play"]["recent"]["n"] == 1
    assert k["retention"]["d7"] == pytest.approx(1 / 7, abs=0.001)
    a = client.get("/v1/listening-profile").json()["abilities"]
    assert a["conversation"]["n"] == 1 and a["conversation"]["provisional"]


def test_recommendations_follow_interests_and_focus_clip_is_served_first(client):
    client.post("/onboarding/skip", json={"level": 3})
    client.put("/settings", json={"interests": ["tech"]})
    rec = client.get("/v1/recommendations").json()
    assert rec["items"] and rec["items"][0]["topic"] == "tech"
    assert client.put("/settings", json={"interests": ["cooking"]}).status_code == 422
    target = rec["items"][1]["id"] if len(rec["items"]) > 1 else rec["items"][0]["id"]
    s = client.post("/sessions", json={"minutes": 30, "focus_clip": target}).json()
    for _ in range(80):
        item = client.post(f"/sessions/{s['id']}/next", json={"elapsed": 5}).json()["item"]
        if item["type"] == "comprehension":
            assert item["clip"]["id"] == target
            break
        client.post(f"/sessions/{s['id']}/answer", json={"item_id": item["item_id"], **solve(item)})
    else:
        raise AssertionError("no comprehension item")


def test_growth_when_missed_word_is_heard_in_new_audio(client):
    from app import vocab
    with db.tx() as conn:
        conn.execute("INSERT INTO attempts (session_id, created_at, date, kind, block, item_id, level, score, detail)"
                     " VALUES (NULL,?,?,'word','diag','allocate',2,0.0,'{}')", (db.now_iso(), db.local_date()))
        vocab.record_context(conn, "allocate", True)
        vocab.record_context(conn, "allocate", True)       # same day: one event
        vocab.record_context(conn, "postpone", True)       # never missed: no event
    items = client.get("/v1/growth").json()["items"]
    assert [i["ref"] for i in items] == ["allocate"]


def test_word_forgotten_after_being_acquired(client):
    from app import vocab
    with db.tx() as conn:
        for m in ("text", "audio", "context"):
            vocab.update_mastery(conn, "allocate", m, 1.0)
        row = conn.execute("SELECT * FROM user_vocab WHERE word_id='allocate'").fetchone()
        assert vocab.status_of(row) == "acquired"
        vocab.update_mastery(conn, "allocate", "audio", 0.0)     # missed it later
        row = conn.execute("SELECT * FROM user_vocab WHERE word_id='allocate'").fetchone()
        assert vocab.status_of(row) == "forgotten"
        vocab.update_mastery(conn, "allocate", "audio", 1.0)     # one correct answer is not enough (0.75)
        row = conn.execute("SELECT * FROM user_vocab WHERE word_id='allocate'").fetchone()
        assert vocab.status_of(row) == "forgotten"
        vocab.update_mastery(conn, "allocate", "audio", 1.0)     # relearned
        row = conn.execute("SELECT * FROM user_vocab WHERE word_id='allocate'").fetchone()
        assert row["audio_mastery"] >= vocab.ACQUIRED and vocab.status_of(row) == "acquired"


def test_comprehension_choices_are_shuffled_so_position_does_not_score(client):
    """The authored questions mostly put the right answer second; serving must not leak that."""
    from app import engine
    lib = library()
    clips = [c for c in lib.clips.values() if c.questions and len(c.questions[0]["choices"]) == 4]
    hits = total = 0
    positions = set()
    for c in clips:
        for _ in range(5):
            p = engine._comprehension_payload(c, False, "new")
            for q, pq, a in zip(c.questions, p["public"]["questions"], p["secret"]["answers"]):
                assert pq["choices"][a] == q["choices"][q["answer"]]        # grading key follows the shuffle
                positions.add(a)
                hits += a == 1
                total += 1
    assert positions == {0, 1, 2, 3}
    assert 0.15 < hits / total < 0.35          # "always pick 2" is back to chance (25%), not ~80%
    # end to end: answers are graded against the shuffled order
    client.post("/onboarding/skip", json={"level": 2})
    s = client.post("/sessions", json={"minutes": 15}).json()
    for _ in range(60):
        item = client.post(f"/sessions/{s['id']}/next", json={"elapsed": 5}).json()["item"]
        body = {"item_id": item["item_id"], **solve(item)}
        fb = client.post(f"/sessions/{s['id']}/answer", json=body).json()
        if item["type"] == "comprehension":
            assert fb["score"] == 1.0 and all(r["correct"] for r in fb["results"])
            break



def test_checkpoint_forms_are_comparable():
    """Objective proxies of difficulty stay within fixed tolerances across the five forms."""
    from app import checkpoints
    from app.content import reload_library
    reload_library()
    e = checkpoints.equivalence()
    assert e["ok"], [c for c in e["checks"] if not c["ok"]]
    assert {c["metric"] for c in e["checks"]} >= {"structure", "words", "words_per_second", "words_per_line",
                                                  "long_word_share", "gist_inference"}


def test_checkpoint_order_is_random_per_learner_and_stable(client):
    from app import progress
    plan = progress.checkpoint_plan()
    assert [p["day"] for p in plan] == [1, 15, 29, 43, 57]
    assert sorted(p["form"] for p in plan) == ["A", "B", "C", "D", "E"]
    assert progress.checkpoint_plan() == plan                      # persisted, not redrawn
    st = client.get("/profile").json()["tests"]
    assert [t["form"] for t in st] == [p["form"] for p in plan] and st[0]["name"] == "初回レベル診断"
    # fresh plans differ across learners (120 possible orders; 30 draws all equal would be ~impossible)
    seen = set()
    for _ in range(30):
        with db.tx() as conn:
            conn.execute("DELETE FROM checkpoint_plan")
            seen.add(tuple(p["form"] for p in progress.checkpoint_plan(conn)))
    assert len(seen) > 5


def test_profile_diagnostic_uses_first_assigned_form_and_taken_forms_keep_their_slot(client):
    from app import progress
    with db.tx() as conn:
        conn.execute("DELETE FROM checkpoint_plan")
        for i, (d, f) in enumerate(zip(progress.CHECKPOINT_DAYS, "DABEC"), 1):
            conn.execute("INSERT INTO checkpoint_plan (slot, day, form, assigned_at) VALUES (?,?,?,?)", (i, d, f, db.now_iso()))
    sid, seen = _profile_session(client, sound_good=True)
    t = db.q1("SELECT form FROM tests")
    assert t["form"] == "D"                                        # slot 1 of this learner, not a fixed "A"
    asked = [r["item_id"] for r in db.q("SELECT item_id FROM attempts WHERE session_id=? AND kind='comprehension'", (sid,))]
    assert all(a.startswith("tD-") for a in asked) and client.get("/profile").json()["onboarded"]
    # if the plan is ever rebuilt, a taken form stays in the slot it was taken in
    with db.tx() as conn:
        conn.execute("DELETE FROM checkpoint_plan")
    assert progress.checkpoint_plan()[0]["form"] == "D"
    ov = client.get("/v1/checkpoints").json()
    assert ov["plan"][0]["result"]["form"] == "D" and ov["equivalence"]["ok"]
