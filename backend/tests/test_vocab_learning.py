"""Word book: NGSL/TSL word set, written-first placement, word lessons, phase separation, migration."""
import json
import random
import sqlite3

import pytest

from app import access, config, db, vocab, vocab_lessons
from app.vocab_data import VOCAB_DIR, VocabLibrary, sense_matches, validate_dataset, vocab_library
from scripts import vocab_candidates

NEW = VOCAB_DIR / "ngsl_tsl.json"
VIA_WEB = {"x-forwarded-host": "192.168.0.10:3210"}


# --- word set ------------------------------------------------------------------------------------

def test_ngsl_tsl_dataset_is_traceable_to_the_official_lists():
    data = json.loads(NEW.read_text())
    assert validate_dataset(data) == []
    meta = data["dataset"]
    assert meta["license"] == "CC-BY-SA-4.0" and "newgeneralservicelist.com" in meta["source"]
    assert set(meta["files"]) == {"TSL_12_stats.csv", "NGSL_12_stats.csv"}
    official = {(r["source"], r["lemma"]): r for name in ("tsl", "ngsl") for r in vocab_candidates.read_list(name)}
    for w in data["words"]:
        r = official.get((w["source"], w["lemma"]))
        assert r and r["rank"] == w["rank"], f"{w['lemma']}: not at that rank in the official {w['source']} file"
        lo, hi = vocab_candidates.RANGES[w["source"]]
        assert lo <= w["rank"] <= hi
        assert [e["role"] for e in w["examples"]] == ["study", "transfer"]
        assert w["cefr_level"] is None and w["toeic_relevance"] is None     # nothing the lists don't provide
    assert len(data["words"]) >= 900
    # together with the original set: about 1,000 candidates, no word twice
    lib = vocab_library()
    lemmas = [w.lemma for w in lib.words.values()]
    assert len(lemmas) == len(set(lemmas)) and len(lemmas) >= 1100
    covered = {w.lemma for w in lib.words.values()}
    cands = vocab_candidates.candidates()
    excluded = {x["lemma"] for x in meta["excluded"]}
    assert all(c["lemma"] in covered or c["lemma"] in excluded for c in cands)
    assert not vocab_library().missing_audio(), "run scripts/build_audio.py"


def test_checksum_of_the_source_files_is_enforced(tmp_path, monkeypatch):
    bad = tmp_path / "TSL_12_stats.csv"
    bad.write_text("Word,TSL Rank,SFI,U\nhello,1,1,1\n")
    monkeypatch.setattr(vocab_candidates, "SOURCES", tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        vocab_candidates.read_list("tsl")


def test_validator_detects_wrong_sense_copied_transfer_and_duplicates(tmp_path):
    data = json.loads(NEW.read_text())
    good = data["words"][0]
    w = json.loads(json.dumps(good))
    # a translation that does not carry the gloss: the sentence uses another meaning of the word
    w["examples"][0]["ja"] = "まったく関係のない訳です。"
    w["examples"][1] = {**w["examples"][0], "role": "transfer", "en": w["examples"][0]["en"].replace(".", " today.")}
    probs = "\n".join(validate_dataset({"dataset": data["dataset"], "words": [w] + data["words"][1:60]}))
    assert "sense mismatch" in probs
    assert "too close to the study sentence" in probs
    assert sense_matches("延期する", "会議は来週に延期されました") and not sense_matches("延期する", "会議は来週です")
    assert sense_matches("結ぶ", "靴ひもを結びました")
    # the same word in two dataset files is refused at load
    core = json.loads((VOCAB_DIR / "toeic_core.json").read_text())
    dup = {"dataset": data["dataset"], "words": [dict(core["words"][0], id="dup-1")] + data["words"][:60]}
    p = tmp_path / "extra.json"
    p.write_text(json.dumps(dup, ensure_ascii=False))
    with pytest.raises(ValueError, match="duplicate across datasets"):
        VocabLibrary([VOCAB_DIR / "toeic_core.json", p])


def test_transfer_questions_have_one_right_answer_and_their_own_audio():
    lib = vocab_library()
    rng = random.Random(5)
    words = [w for w in lib.words.values() if w.transfer]
    assert len(words) >= 900
    held = [w for w in words if lib.held(w, "transfer")]
    for w in held:
        assert vocab.build_item(w, "context", rng, sentence="transfer") is None, w.id
    for w in rng.sample([w for w in words if not lib.held(w, "transfer")], 150):
        p = vocab.build_item(w, "context", rng, sentence="transfer", phase="transfer")
        assert p, w.id
        opts = [lib.words[i] for i in p["secret"]["options"]]
        assert [o.id for o in opts if o.appears_in(w.transfer)] == [w.id], w.id
        a = lib.files(w)
        assert p["public"]["audio"] == a["transfer"] != a["example"]
        assert w.lemma not in p["public"]["audio"].rsplit("/", 1)[-1]
        assert p["public"]["transfer"] is True


# --- placement --------------------------------------------------------------------------------------

def _secret(item_id):
    return json.loads(db.q1("SELECT payload FROM vocab_items WHERE id=?", (item_id,))["payload"])["secret"]


def run_placement(client, good):
    d = client.post("/v1/vocabulary/diagnostics", json={"style": "placement"}).json()
    seen = []
    for _ in range(80):
        r = client.post(f"/v1/vocabulary/diagnostics/{d['id']}/next").json()
        if r["done"]:
            return r, seen
        sec = _secret(r["item"]["item_id"])
        ok = good(sec)
        seen.append({**sec, "ok": ok})
        body = {"item_id": r["item"]["item_id"], "choice": sec["answer"] if ok else None, "ms": 2500}
        fb = client.post("/v1/vocabulary/answers", json=body).json()
        assert "meaning" not in fb, "answers are not revealed during the placement test"
    raise AssertionError("placement did not finish")


def test_placement_is_written_first_and_routes_each_word(client):
    lib = vocab_library()

    def good(sec):  # knows easy words on paper, cannot hear tier>=2 words
        w = lib.words[sec["word_id"]]
        return w.tier <= 2 and (sec["modality"] == "text" or w.tier == 1)

    r, seen = run_placement(client, good)
    res = r["diagnostic"]["result"]
    assert res["style"] == "placement"
    assert 30 <= res["items"] <= 40
    assert seen[0]["modality"] == "text"
    # by-ear checks only for words that were answered correctly in writing, and only after other questions
    for i, s in enumerate(seen):
        if s["modality"] == "audio":
            first = next(j for j, t in enumerate(seen) if t["word_id"] == s["word_id"])
            assert seen[first]["modality"] == "text" and seen[first]["ok"] and i - first >= 2
        if s["modality"] == "context":
            assert any(t["word_id"] == s["word_id"] and t["modality"] == "audio" and t["ok"] for t in seen[:i])
    assert sum(1 for s in seen if s["modality"] == "text") > len(seen) / 2
    routes = {k: {w["id"] for w in v} for k, v in res["routes"].items()}
    for s in seen:
        if s["modality"] == "text" and not s["ok"]:
            assert s["word_id"] in routes["basic"]
        if s["modality"] == "audio" and not s["ok"]:
            assert s["word_id"] in routes["sound"]
    assert routes["basic"] and res["unknown"] > 0
    assert res["known_estimate"] is not None and 0 <= res["known_estimate"] <= res["total_words"]
    att = [json.loads(a["detail"]) for a in db.q("SELECT detail FROM attempts WHERE kind='word'")]
    assert att and all(a["phase"] == "placement" for a in att)
    assert all(a["ms"] == 2500 for a in att), "response time is recorded"


def test_placement_and_listening_diagnostic_do_not_resume_each_other(client):
    p = client.post("/v1/vocabulary/diagnostics", json={"style": "placement"}).json()
    item = client.post(f"/v1/vocabulary/diagnostics/{p['id']}/next").json()["item"]
    assert _secret(item["item_id"])["modality"] == "text"
    listening = client.post("/v1/vocabulary/diagnostics", json={}).json()
    assert listening["id"] != p["id"] and not listening["resumed"]
    again = client.post("/v1/vocabulary/diagnostics", json={"style": "placement"}).json()
    assert again["id"] == p["id"] and again["resumed"]


def test_one_good_day_is_not_acquired(client):
    w = vocab_library().words["postpone"]
    with db.tx() as conn:
        for mod in ("text", "audio", "context"):
            p = vocab.build_item(w, mod)
            fb = vocab.grade(conn, p, {"choice": p["secret"]["answer"], "ms": 1000}, origin="test")
    assert fb["status"] == "confirm"
    # the next day, right again in another form -> acquired
    with db.tx() as conn:
        conn.execute("UPDATE user_vocab SET last_correct_date='2000-01-01' WHERE word_id='postpone'")
        p = vocab.build_item(w, "audio")
        fb = vocab.grade(conn, p, {"choice": p["secret"]["answer"], "ms": 1000}, origin="test")
    assert fb["status"] == "acquired"


# --- lessons ---------------------------------------------------------------------------------------

def run_lesson(client, minutes=5, good=lambda sec: True, ms=6000, recall="yes"):
    les = client.post("/v1/vocabulary/lessons", json={"minutes": minutes}).json()
    log = []
    for _ in range(400):
        r = client.post(f"/v1/vocabulary/lessons/{les['id']}/next").json()
        if r["done"]:
            return les["id"], r, log
        item = r["item"]
        if item["type"] == "word_study":
            fb = client.post("/v1/vocabulary/answers", json={"item_id": item["item_id"], "recall": recall, "ms": ms}).json()
            log.append({"type": "study", "word": fb["lemma"], "fb": fb, "item": item})
        else:
            sec = _secret(item["item_id"])
            ok = good(sec)
            fb = client.post("/v1/vocabulary/answers", json={"item_id": item["item_id"], "ms": ms,
                                                             "choice": sec["answer"] if ok else (sec["answer"] + 1) % 4}).json()
            log.append({"type": "word", "sec": sec, "fb": fb, "item": item})
    raise AssertionError("lesson did not finish")


def test_lesson_study_then_ear_then_example_then_unseen_sentence(client):
    lib = vocab_library()
    lid, done, log = run_lesson(client, minutes=5)
    studied = [x for x in log if x["type"] == "study"]
    assert studied, "a lesson teaches words"
    first = studied[0]
    wid = next(w.id for w in lib.words.values() if w.lemma == first["word"])
    # the study card shows the word first; meaning, scene and example come back only after the recall answer
    assert "meaning" not in first["item"] and first["fb"]["meaning"] and first["fb"]["example_ja"]
    checks = [x for x in log if x["type"] == "word" and x["sec"]["word_id"] == wid]
    kinds = [(c["sec"]["modality"], c["sec"]["sentence"], c["sec"]["phase"]) for c in checks]
    assert kinds[:3] == [("audio", "example", "first"), ("context", "example", "first"), ("context", "transfer", "transfer")]
    tr = checks[2]
    assert tr["item"]["transfer"] and tr["fb"]["transfer"] == lib.words[wid].transfer and tr["fb"]["phase"] == "transfer"
    i_study = log.index(first)
    assert all(log.index(c) > i_study + 1 for c in checks), "checks come after other items, not right away"
    # studied words are enrolled in FSRS even when every check was right
    assert db.q1("SELECT 1 FROM srs_cards WHERE id=?", (f"w_audio:{wid}",))
    res = done["lesson"]["result"]
    assert res["phases"]["first"]["accuracy"] == 1.0 and res["phases"]["transfer"]["n"] >= 1
    assert done["lesson"]["spent_s"] <= 5 * 60 + vocab_lessons.OVERRUN_S + 130


def test_study_self_report_changes_no_ability(client):
    les = client.post("/v1/vocabulary/lessons", json={"minutes": 5}).json()
    item = client.post(f"/v1/vocabulary/lessons/{les['id']}/next").json()["item"]
    assert item["type"] == "word_study"
    client.post("/v1/vocabulary/answers", json={"item_id": item["item_id"], "recall": "yes"})
    wid = _secret(item["item_id"])["word_id"]
    row = db.q1("SELECT * FROM user_vocab WHERE word_id=?", (wid,))
    assert row["text_n"] == row["audio_n"] == row["context_n"] == 0
    assert vocab.status_of(row) == "untested"
    assert not db.q1("SELECT 1 FROM srs_cards WHERE ref=?", (wid,))


def test_lesson_answers_are_counted_once(client):
    les = client.post("/v1/vocabulary/lessons", json={"minutes": 10}).json()
    item = client.post(f"/v1/vocabulary/lessons/{les['id']}/next").json()["item"]
    # tapping "next" twice / another device: the same open item comes back
    again = client.post(f"/v1/vocabulary/lessons/{les['id']}/next").json()["item"]
    assert again["item_id"] == item["item_id"]
    body = {"item_id": item["item_id"], "recall": "no", "ms": 5000}
    a = client.post("/v1/vocabulary/answers", json=body).json()
    b = client.post("/v1/vocabulary/answers", json=body).json()
    assert b["duplicate"] and a["lesson"]["answered"] == 1
    assert client.get(f"/v1/vocabulary/lessons/{les['id']}").json()["answered"] == 1
    assert client.post("/v1/vocabulary/answers", json={**body, "recall": "yes"}).status_code == 409
    assert db.q1("SELECT COUNT(*) n FROM attempts WHERE kind='word_study'")["n"] == 1


def test_unseen_and_review_results_are_kept_apart(client):
    # right on the checks right after studying, wrong on every unseen sentence
    lid, done, log = run_lesson(client, minutes=5, good=lambda sec: sec.get("sentence") != "transfer")
    res = done["lesson"]["result"]
    assert res["phases"]["first"]["accuracy"] == 1.0
    assert res["phases"]["transfer"]["accuracy"] == 0.0
    ov = client.get("/v1/vocabulary/overview").json()
    # reviews (correct here) never raise the unseen-sentence score
    assert ov["phases"]["transfer"]["accuracy"] == 0.0 and ov["phases"]["first"]["accuracy"] == 1.0
    # FSRS: make everything due; the next lesson serves reviews before any new word, logged as reviews only
    with db.tx() as conn:
        conn.execute("UPDATE srs_cards SET due='2000-01-01T00:00:00+00:00' WHERE kind LIKE 'w_%'")
    due = vocab.due_count()
    lid2, done2, log2 = run_lesson(client, minutes=5)
    reviews = [x for x in log2 if x["type"] == "word" and x["sec"]["phase"] == "review"]
    assert len(reviews) >= 3 and all(x["sec"]["card"] for x in reviews)
    studies = [i for i, x in enumerate(log2) if x["type"] == "study"]
    assert not studies or log2.index(reviews[0]) < studies[0]
    assert vocab.due_count() < due
    reviewed = db.q1("SELECT * FROM srs_cards WHERE id=?", (reviews[0]["sec"]["card"],))
    assert reviewed["due"] > db.now_iso(), "a reviewed card is scheduled into the future"
    ov2 = client.get("/v1/vocabulary/overview").json()
    assert ov2["phases"]["review"]["n"] >= 3
    assert ov2["phases"]["transfer"]["n"] == ov["phases"]["transfer"]["n"] + done2["lesson"]["result"]["phases"]["transfer"]["n"]


def test_lesson_starts_from_placement_weak_words(client):
    lib = vocab_library()
    r, seen = run_placement(client, lambda sec: lib.words[sec["word_id"]].tier == 1 and sec["modality"] == "text")
    basic = [w["id"] for w in r["diagnostic"]["result"]["routes"]["basic"]]
    assert basic
    # missed words are new words to study, not FSRS reviews: the placement makes no card, so the first lesson
    # opens with studying (not with reviews of words never taught), starting from the placement's "basic" route
    assert not db.q1("SELECT 1 FROM srs_cards WHERE kind LIKE 'w_%'")
    assert vocab.due_count() == 0
    lid, done, log = run_lesson(client, minutes=5)
    assert log[0]["type"] == "study"
    assert not [x for x in log if x["type"] == "word" and x["sec"].get("phase") == "review"]
    first = log[0]
    wid = next(w.id for w in lib.words.values() if w.lemma == first["word"])
    assert wid in basic and "診断" in first["item"]["reason"]
    # once studied and checked in the lesson, the word is in FSRS
    assert db.q1("SELECT 1 FROM srs_cards WHERE ref=?", (wid,))


def test_lesson_minutes_and_overview_counts(client):
    assert client.post("/v1/vocabulary/lessons", json={"minutes": 7}).status_code == 422
    ov = client.get("/v1/vocabulary/overview").json()
    assert ov["minutes"] == [5, 10, 15, 30]
    assert ov["untested"] == ov["total"] == len(vocab_library().words) and ov["acquired"] == 0
    run_lesson(client, minutes=5)
    ov = client.get("/v1/vocabulary/overview").json()
    assert ov["learning"] > 0 and ov["untested"] < ov["total"]
    assert ov["acquired"] == 0, "a word studied today is not acquired yet"
    assert ov["acquired"] + ov["learning"] + ov["untested"] == ov["total"]


def test_word_book_needs_a_signed_in_device(client, monkeypatch):
    monkeypatch.setenv("LISTENFLOW_ACCESS_CODE", "1234-5678-9012")
    monkeypatch.delenv("LISTENFLOW_AUTH", raising=False)
    access.reset_failures()
    for method, path, body in (("get", "/v1/vocabulary/overview", None), ("post", "/v1/vocabulary/lessons", {"minutes": 5}),
                               ("post", "/v1/vocabulary/lessons/x/next", {}), ("get", "/v1/vocabulary/lessons/x", None),
                               ("post", "/v1/vocabulary/answers", {"item_id": "x", "recall": "yes"}),
                               ("post", "/v1/vocabulary/diagnostics", {"style": "placement"})):
        r = getattr(client, method)(path, headers=VIA_WEB, **({"json": body} if body is not None else {}))
        assert r.status_code == 401, path


# --- migration -----------------------------------------------------------------------------------

def test_migration_keeps_learning_data_and_backs_it_up_first(client):
    client.post("/onboarding/skip", json={"level": 3})
    with db.tx() as conn:
        conn.execute("INSERT INTO user_vocab (word_id, text_mastery, text_n, first_seen_at) VALUES ('postpone', 1.0, 1, ?)",
                     (db.now_iso(),))
        for day in ("2026-01-01", "2026-01-03"):
            conn.execute("INSERT INTO attempts (created_at, date, kind, block, item_id, level, score, detail)"
                         " VALUES (?,?,?,?,?,?,?,?)", (db.now_iso(), day, "word", "diag", "postpone", 3, 1.0, "{}"))
    db.reset_connection()
    # turn the database back into the previous version's schema
    con = sqlite3.connect(config.DB_PATH)
    con.execute("DROP TABLE vocab_lessons")
    con.execute("ALTER TABLE user_vocab DROP COLUMN correct_days")
    con.execute("ALTER TABLE user_vocab DROP COLUMN last_correct_date")
    con.commit()
    con.close()
    backups = config.DATA_DIR / "backups"
    before = set(backups.glob("pre-migration-*.db")) if backups.exists() else set()
    row = db.q1("SELECT * FROM user_vocab WHERE word_id='postpone'")      # reconnect -> migrate
    assert row["text_mastery"] == 1.0 and row["correct_days"] == 2 and row["last_correct_date"] == "2026-01-03"
    assert db.q1("SELECT level FROM profile")["level"] == 3
    made = set(backups.glob("pre-migration-*.db")) - before
    assert len(made) == 1
    old = sqlite3.connect(made.pop())
    assert old.execute("SELECT text_mastery FROM user_vocab WHERE word_id='postpone'").fetchone()[0] == 1.0
    assert not old.execute("SELECT 1 FROM sqlite_master WHERE name='vocab_lessons'").fetchone()
    old.close()
    # a second start does not back up again
    db.reset_connection()
    db.q1("SELECT 1")
    assert len(set(backups.glob("pre-migration-*.db")) - before) == 1


def test_relearned_word_heard_correctly_is_no_longer_unknown(client):
    w = vocab_library().words["bookstore"]
    with db.tx() as conn:
        p = vocab.build_item(w, "text", phase="placement")
        vocab.grade(conn, p, {"choice": None}, origin="diag")          # did not know it in writing
        assert vocab.status_of(conn.execute("SELECT * FROM user_vocab WHERE word_id='bookstore'").fetchone()) == "unknown"
        p = vocab.build_item(w, "audio", phase="first")
        fb = vocab.grade(conn, p, {"choice": p["secret"]["answer"], "ms": 1500}, origin="learn")
    assert fb["mastery"]["text"] >= 0.5 and fb["status"] != "unknown"


def test_placement_estimate_is_never_written_to_words(client):
    """30 questions cannot settle 1,147 words: only asked words get a state; nothing is acquired from it."""
    r, seen = run_placement(client, lambda sec: True)
    res = r["diagnostic"]["result"]
    asked = {s["word_id"] for s in seen}
    rows = {x["word_id"] for x in db.q("SELECT word_id FROM user_vocab")}
    assert rows == asked
    assert res["known_estimate"] > len(asked)                # the estimate covers far more words than were asked
    ov = client.get("/v1/vocabulary/overview").json()
    assert ov["acquired"] == 0, "a perfect placement still acquires nothing (one day, and 4-choice can be luck)"
    assert ov["placement"]["tested_words"] == len(asked)
    assert ov["placement"]["estimated_known"] == res["known_estimate"]
    assert ov["untested"] == ov["total"] - len(asked)


def test_held_audio_is_not_used_until_released(client, tmp_path, monkeypatch):
    from app import vocab_data
    lib = vocab_library()
    w = lib.words["clerk"]
    review = tmp_path / "audio_review.json"
    review.write_text(json.dumps({"items": {"clerk:example": {"text": w.example, "status": "hold", "reason": "asr"}}}))
    monkeypatch.setattr(vocab_data, "AUDIO_REVIEW_PATH", review)
    lib = vocab_data.reload_vocab_library()
    try:
        assert "example" not in lib.audio(w) and lib.held_items() == ["clerk:example"]
        assert vocab.build_item(w, "context") is None                 # the sentence is not asked
        assert vocab.build_item(w, "context", sentence="transfer")   # the other recording still is
        assert "clerk" not in lib.missing_audio()                     # held is not missing
        review.write_text(json.dumps({"items": {"clerk:example": {"text": w.example, "status": "ok"}}}))
        assert "example" in vocab_data.reload_vocab_library().audio(w)
        # an entry for an older wording does not hold the new sentence
        review.write_text(json.dumps({"items": {"clerk:example": {"text": "old wording", "status": "hold"}}}))
        assert "example" in vocab_data.reload_vocab_library().audio(w)
    finally:
        monkeypatch.undo()
        vocab_data.reload_vocab_library()
