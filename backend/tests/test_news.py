"""Daily English News: feeds, story selection, the fact check, audio, the morning run and the learner's flow.

Gemini and speech are replaced by stand-ins (no network, no API cost). The story below is made up."""
import copy
import datetime as dt
import json
import shutil
import subprocess

import pytest

from app import access, config, db, news, news_study, tts, vocab_lessons
from app import news_feeds as F
from app.vocab_data import reload_vocab_library, vocab_library

VIA_WEB = {"x-forwarded-host": "192.168.0.10:3210"}   # a request through the web app (not this Mac directly)

NOW = dt.datetime.now(dt.timezone.utc)
PUB = (NOW - dt.timedelta(hours=3)).strftime("%a, %d %b %Y %H:%M:%S GMT")
OLD = (NOW - dt.timedelta(days=9)).strftime("%a, %d %b %Y %H:%M:%S GMT")

SUMMARY = ("Voltra, an electric bus manufacturer, said on Tuesday it would open a factory in Leeds next year, creating "
           "400 jobs. The company expects to build 1,200 buses a year at the site. The council welcomed the plan, but "
           "union leaders said wages at the firm's other factories were too low. Voltra said the £85m investment "
           "depended on government support for charging stations.")


def rss(items: list[tuple[str, str, str, str]]) -> bytes:
    from xml.sax.saxutils import escape
    body = "".join(f"<item><title>{escape(t)}</title><link>{escape(u)}</link><description><![CDATA[<p>{d}</p>]]></description>"
                   f"<pubDate>{p}</pubDate></item>" for t, u, d, p in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{body}</channel></rss>'.encode()


FEED_DATA = {
    "https://www.theguardian.com/uk/technology/rss": rss([
        ("Electric bus maker Voltra to open Leeds factory, creating 400 jobs",
         "https://www.theguardian.com/business/2026/voltra-leeds?CMP=share&utm_source=x", SUMMARY, PUB),
        ("Tech shares fall – business live", "https://www.theguardian.com/business/live/2026/x", SUMMARY, PUB),
        ("Old story about phones and batteries in shops today", "https://www.theguardian.com/old", SUMMARY, OLD),
        ("Short teaser only", "https://www.theguardian.com/short", "Too short to learn from.", PUB),
    ]),
    "https://feeds.bbci.co.uk/news/technology/rss.xml": rss([
        ("Voltra electric bus factory for Leeds promises 400 jobs", "https://www.bbc.co.uk/news/voltra", "Short.", PUB),
    ]),
}


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(req, timeout=None):
    url = req.full_url
    if url not in FEED_DATA:
        raise OSError("offline")
    return FakeResponse(FEED_DATA[url])


def q(text, choices, answer, evidence):
    return {"q": text, "choices": choices, "answer": answer, "evidence": evidence, "explanation": "本文にそう述べられています。"}


GOOD = {
    "status": "ok", "title_en": "Voltra plans a bus factory in Leeds", "title_ja": "Voltra、リーズにバス工場を計画",
    "summary_ja": "電気バスメーカーの Voltra がリーズに工場を開き、400人の雇用を生む予定です。",
    "levels": {
        "A2": {"sentences": [
            {"en": "Voltra makes electric buses.", "ja": "Voltra は電気バスを作っています。"},
            {"en": "The company plans to open a new factory in Leeds.", "ja": "その会社はリーズに新しい工場を開く予定です。"},
            {"en": "The factory will bring 400 jobs to the area.", "ja": "工場はその地域に400人分の仕事をもたらします。"},
            {"en": "Voltra hopes to make 1,200 buses there every year.", "ja": "Voltra はそこで毎年1,200台のバスを作りたいと考えています。"},
            {"en": "Some union leaders are not happy about pay at other Voltra factories.", "ja": "一部の労働組合の幹部は、Voltra の他の工場の賃金に不満です。"},
        ], "questions": [
            q("工場はどこに作られますか？", ["リーズ", "ロンドン", "マンチェスター", "バーミンガム"], 0, [1]),
            q("何人分の仕事が生まれますか？", ["40人", "400人", "1,200人", "85人"], 1, [2]),
            q("組合の幹部は何に不満ですか？", ["工場の場所", "バスの色", "他の工場の賃金", "充電の料金"], 2, [4]),
        ]},
        "B1": {"sentences": [
            {"en": "Voltra, which builds electric buses, announced on Tuesday that it will open a factory in Leeds.",
             "ja": "電気バスを作る Voltra は火曜日、リーズに工場を開くと発表した。"},
            {"en": "The new site is expected to create 400 jobs.", "ja": "新しい拠点では400人の雇用が生まれる見込みだ。"},
            {"en": "The company says it could produce 1,200 buses there each year.", "ja": "会社によると、そこで毎年1,200台のバスを生産できるという。"},
            {"en": "Local council officials welcomed the plan.", "ja": "地元の議会はこの計画を歓迎した。"},
            {"en": "However, union leaders complained that wages at Voltra's other factories are too low.",
             "ja": "しかし労働組合の幹部は、Voltra の他の工場の賃金が低すぎると不満を言った。"},
            {"en": "The investment of £85m depends on government support for charging stations.",
             "ja": "8500万ポンドの投資は、充電設備への政府の支援が条件だ。"},
        ], "questions": [
            q("Voltra はいつ発表しましたか？", ["月曜日", "火曜日", "金曜日", "日曜日"], 1, [0]),
            q("投資の条件は何ですか？", ["新しい社長", "充電設備への政府の支援", "工場の売却", "バスの値下げ"], 1, [5]),
            q("組合の幹部は何について不満を言いましたか？", ["工場の場所", "賃金", "バスの数", "議会の決定"], 1, [4]),
        ]},
        "B2": {"sentences": [
            {"en": "Electric bus manufacturer Voltra has announced plans for a factory in Leeds, due to open next year.",
             "ja": "電気バスメーカーの Voltra は、来年開業予定のリーズの工場計画を発表した。"},
            {"en": "The project is expected to create around 400 jobs.", "ja": "この事業で約400人の雇用が生まれる見込みだ。"},
            {"en": "According to the company, the plant could turn out 1,200 buses annually.", "ja": "会社によると、工場は年間1,200台のバスを生産できる。"},
            {"en": "The local council has welcomed the decision.", "ja": "地元の議会はこの決定を歓迎している。"},
            {"en": "Union leaders, however, argue that pay at the firm's existing factories is too low.",
             "ja": "しかし労働組合の幹部は、同社の既存工場の賃金は低すぎると主張している。"},
            {"en": "Voltra has made clear that its £85m investment depends on government backing.",
             "ja": "Voltra は、8500万ポンドの投資は政府の支援次第だと明言している。"},
            {"en": "In particular, the firm wants public support for charging stations.", "ja": "特に同社は、充電設備への公的支援を求めている。"},
        ], "questions": [
            q("工場はいつ開業する予定ですか？", ["今年", "来年", "5年後", "未定"], 1, [0]),
            q("工場の年間生産能力は？", ["400台", "850台", "1,200台", "12,000台"], 2, [2]),
            q("議会の反応は？", ["反対した", "歓迎した", "延期を求めた", "調査を始めた"], 1, [3]),
        ]},
    },
    "vocabulary": [
        {"lemma": "announce", "pos": "v", "meaning": "発表する", "form": "announced", "sentence": 0,
         "transfer": {"en": "The school announced that classes will start an hour later next month.", "form": "announced",
                      "ja": "学校は来月から授業が1時間遅く始まると発表した。"}},
        {"lemma": "complain", "pos": "v", "meaning": "不満を言う", "form": "complained", "sentence": 4,
         "transfer": {"en": "Several customers complained about the long wait at the front desk.", "form": "complained",
                      "ja": "何人かの客がフロントでの長い待ち時間について不満を言った。"}},
        {"lemma": "investment", "pos": "n", "meaning": "投資", "form": "investment", "sentence": 5,
         "transfer": {"en": "Buying a good laptop was a smart investment for my studies.", "form": "investment",
                      "ja": "良いノートパソコンを買ったことは、勉強のための賢い投資だった。"}},
        {"lemma": "wage", "pos": "n", "meaning": "賃金", "form": "wages", "sentence": 4,
         "transfer": {"en": "The new job offers higher wages and more paid holidays.", "form": "wages",
                      "ja": "新しい仕事はより高い賃金とより多くの有給休暇を提供する。"}},
        {"lemma": "voltra", "pos": "n", "meaning": "ボルトラ", "form": "Voltra", "sentence": 0,
         "transfer": {"en": "x", "form": "x", "ja": "x"}},
    ],
}


@pytest.fixture()
def newsenv(client, tmp_path, monkeypatch):
    """Fake feeds, a fake Gemini that returns GOOD, fake speech (a silent mp3) and speech recognition that passes."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed to join the audio")
    audio = tmp_path / "media"
    audio.mkdir()
    monkeypatch.setattr(config, "AUDIO_DIR", audio)
    silent = tmp_path / "silent.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "1",
                    "-b:a", "64k", str(silent)], check=True)
    calls = {"gemini": [], "say": [], "responses": []}

    def fake_gemini(text):
        calls["gemini"].append(text)
        obj = calls["responses"].pop(0) if calls["responses"] else copy.deepcopy(GOOD)
        return obj, {"prompt": 1000, "output": 2000}

    def fake_say(text, voice, rate, mp3):
        calls["say"].append((text, voice, rate))
        mp3.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(silent, mp3)
        return 1.0, voice

    calls["heard"] = lambda text: True
    monkeypatch.setattr(news, "call_gemini", fake_gemini)
    monkeypatch.setattr(tts, "_say_checked", fake_say)
    monkeypatch.setattr(news, "asr_check", lambda items: [calls["heard"](t) for t, _ in items])
    monkeypatch.setattr(news.lessons, "gemini_key", lambda: "test-key")
    monkeypatch.setattr(news_study.threading, "Thread", _NoThread)       # word audio: not in the background
    yield calls
    monkeypatch.undo()
    reload_vocab_library()


class _NoThread:
    def __init__(self, target=None, args=(), daemon=None, **kw):
        self.target, self.args = target, args

    def start(self):
        pass


def _run():
    return news.run("manual", opener=opener)


# --- feeds -----------------------------------------------------------------------------------------

def test_feed_parsing_keeps_headline_summary_link_and_time():
    f = F.FEED_BY_ID["guardian-tech"]
    entries = F.parse_feed(FEED_DATA[f.url], f)
    titles = [e.title for e in entries]
    assert "Tech shares fall – business live" not in titles, "live blogs are skipped"
    e = entries[0]
    assert e.url == "https://www.theguardian.com/business/2026/voltra-leeds", "tracking parameters are removed"
    assert e.summary.startswith("Voltra, an electric bus") and "<p>" not in e.summary
    assert e.published.endswith("+00:00") and e.content == "", "only NASA's full text is kept"
    assert F.entry_id("http://www.theguardian.com/business/2026/voltra-leeds/?utm_source=a") == e.id
    with pytest.raises(F.FeedError):
        F.parse_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>', f)
    assert F.parse_date("2026-11-12T09:00:00Z") == "2026-11-12T09:00:00+00:00"
    assert F.parse_date("not a date") is None


def test_collect_dedupes_and_selection_prefers_corroborated_fresh_stories(client):
    r = news.collect(opener, feeds=[F.FEED_BY_ID["guardian-tech"], F.FEED_BY_ID["bbc-tech"], F.FEED_BY_ID["openai"]])
    assert r["errors"] and "openai" in r["errors"][0], "a feed that fails is reported, the others still count"
    assert news.collect(opener, feeds=[F.FEED_BY_ID["guardian-tech"]])["new"] == 0, "the same stories are not stored twice"
    with db.tx() as conn:
        picks = news.select(conn, ["technology"], 3)
    assert [p["title"] for p in picks] == ["Electric bus maker Voltra to open Leeds factory, creating 400 jobs"]
    assert [c["publisher"] for c in picks[0]["corroborated"]] == ["BBC News"]
    # a 9-day-old story, a teaser too short to learn from and the BBC one-liner are never lessons
    with db.tx() as conn:
        assert news.select(conn, ["business"], 3) == []


# --- the fact check ----------------------------------------------------------------------------------

def _src():
    return {"id": "s1", "feed": "guardian-tech", "publisher": "The Guardian", "category": "technology", "kind": "press",
            "url": "https://x", "title": "Electric bus maker Voltra to open Leeds factory, creating 400 jobs",
            "summary": SUMMARY, "content": "", "published_at": NOW.isoformat()}


def test_verify_accepts_a_faithful_retelling():
    data, problems, notes = news.verify(copy.deepcopy(GOOD), _src())
    assert problems == [], problems
    assert [v["lemma"] for v in data["vocabulary"]] == ["announce", "complain", "investment", "wage"]
    assert any("voltra" in n for n in notes), "a name offered as vocabulary is dropped"
    checks = news.transfer_checks(data, "seed")
    assert len(checks) == 3
    for c in checks:
        assert len(set(c["choices"])) == 4 and c["choices"][c["answer"]] == next(
            v["meaning"] for v in data["vocabulary"] if v["lemma"] == c["lemma"])


@pytest.mark.parametrize("level,i,en,ja,expect", [
    ("B1", 1, "The new site is expected to create 450 jobs.", "450人の雇用", "number 450 "),
    ("B1", 3, "Mayor Smith welcomed the plan on Friday.", "スミス市長は金曜日に歓迎した。", "'Smith'"),
    ("B1", 3, "The council welcomed the plan on Friday.", "議会は金曜日に歓迎した。", "date word 'friday'"),
    ("B1", 1, "The new site is expected to create 400 jobs.", "新しい拠点では多くの雇用が生まれる。", "changes a number"),
    ("B1", 3, 'The council said "we welcome the plan".', "議会は歓迎した。", "quotation"),
    ("B2", 4, "Union leaders said wages at the firm's other factories were too low indeed.", "賃金は低すぎる。", "copied"),
])
def test_verify_rejects_invented_or_changed_facts(level, i, en, ja, expect):
    obj = copy.deepcopy(GOOD)
    obj["levels"][level]["sentences"][i] = {"en": en, "ja": ja}
    _, problems, _ = news.verify(obj, _src())
    assert any(expect in p for p in problems), problems


def test_verify_rejects_bad_questions_and_too_few_words():
    obj = copy.deepcopy(GOOD)
    obj["levels"]["A2"]["questions"][0]["choices"] = ["リーズ", "リーズ", "ロンドン", "パリ"]
    obj["levels"]["B1"]["questions"][1]["evidence"] = [12]
    obj["vocabulary"] = obj["vocabulary"][:1]
    _, problems, _ = news.verify(obj, _src())
    assert any("A2: question needs 4 different" in p for p in problems)
    assert any("B1: question evidence" in p for p in problems)
    assert any("vocabulary entries" in p for p in problems)
    assert news.verify({"status": "insufficient_evidence"}, _src())[1] == ["insufficient_evidence"]


# --- a run: generate, audio, publish; idempotent ------------------------------------------------------

def test_run_publishes_once_and_never_regenerates(newsenv):
    r = _run()
    assert r["status"] in ("ok", "partial") and [x["status"] for x in r["lessons"]] == ["published"]
    assert r["usage"] == {"calls": 1, "prompt": 1000, "output": 2000}
    said = len(newsenv["say"])
    assert said == 5 + 6 + 7 + 3, "every sentence of every level and the 3 unseen sentences"
    row = db.q1("SELECT * FROM news_lessons")
    audio = json.loads(row["audio"])
    assert all(audio["levels"][lv]["status"] == "ok" for lv in news.LEVELS)
    assert (config.AUDIO_DIR / audio["levels"]["B1"]["full"]).exists()
    assert audio["voice"] != audio["transfer_voice"], "unseen sentences are read by another voice"
    # again (another trigger, or the morning run after a manual one): no new story, no Gemini, no speech
    r2 = _run()
    assert r2["usage"]["calls"] == 0 and len(newsenv["say"]) == said
    assert db.q1("SELECT COUNT(*) n FROM news_lessons")["n"] == 1
    # the audio cache: rebuilding the same lesson synthesizes nothing
    data = json.loads(row["data"])
    news.build_audio(row["id"], row["date"], data, data["transfer_checks"])
    assert len(newsenv["say"]) == said
    runs = db.q("SELECT status, detail FROM news_runs ORDER BY started_at")
    assert len(runs) == 2 and json.loads(runs[0]["detail"])["usage"]["calls"] == 1


def test_a_rejected_answer_is_retried_once_with_the_problems_listed(newsenv):
    bad = copy.deepcopy(GOOD)
    bad["levels"]["B1"]["sentences"][1] = {"en": "The new site is expected to create 4,000 jobs.", "ja": "新しい拠点では4,000人の雇用が生まれる見込みだ。"}
    newsenv["responses"] += [bad]
    r = _run()
    assert r["lessons"][0]["status"] == "published" and r["usage"]["calls"] == 2
    assert "4000" in newsenv["gemini"][1] and "automatic fact check" in newsenv["gemini"][1]
    # twice wrong: kept as rejected with the reason, never shown, never generated again
    db.q("DELETE FROM news_lessons")
    newsenv["responses"] += [bad, bad]
    r = _run()
    assert r["lessons"][0]["status"] == "rejected" and "4000" in r["lessons"][0]["reason"]
    assert news_study.overview()["today"] == []
    assert _run()["usage"]["calls"] == 0


def test_audio_that_does_not_match_its_text_is_not_offered(newsenv):
    newsenv["heard"] = lambda text: "1,200 buses annually" not in text        # one B2 sentence is misread twice
    _run()
    row = db.q1("SELECT * FROM news_lessons")
    audio = json.loads(row["audio"])
    assert audio["levels"]["B2"]["status"] == "failed" and audio["levels"]["B1"]["status"] == "ok"
    retried = [s for s in newsenv["say"] if "1,200 buses annually" in s[0]]
    assert len(retried) == 2 and retried[0][1] != retried[1][1], "retried once with another voice"
    card = news_study.overview()["today"][0]
    assert card["levels"] == ["A2", "B1"]
    # every sentence misread: the lesson is not published at all
    db.q("DELETE FROM news_lessons")
    db.q("DELETE FROM news_sources")
    newsenv["heard"] = lambda text: False
    r = _run()
    assert r["lessons"][0]["status"] == "rejected"


# --- the learner's flow -------------------------------------------------------------------------------

def _answer(client, lid, **body):
    return client.post(f"/v1/news/{lid}/attempt", json={"ms": 3000, **body})


def test_listen_first_then_text_then_unseen_sentences(newsenv, client):
    _run()
    ov = client.get("/v1/news").json()
    card = ov["today"][0]
    assert card["title_ja"] is None and card["state"] == "new", "nothing in Japanese before listening"
    assert card["source"]["publisher"] == "The Guardian" and card["source"]["published_at"]
    assert card["corroborated"] == ["BBC News"]
    lid = card["id"]
    v = client.get(f"/v1/news/{lid}?level=B1").json()
    assert v["level"] == "B1" and v["audio"] and len(v["questions"]) == 3
    assert "sentences" not in v and "summary_ja" not in v and "answer" not in v["questions"][0]
    # the unseen sentences wait for the comprehension questions
    assert _answer(client, lid, level="B1", phase="transfer", index=0, choice=0).status_code == 409
    r = _answer(client, lid, level="B1", phase="first", index=0, choice=1).json()
    assert r["done"] is False and "lesson" not in r, "no result before all three are answered"
    again = _answer(client, lid, level="B1", phase="first", index=0, choice=1).json()
    assert again["duplicate"] is True
    assert _answer(client, lid, level="B1", phase="first", index=0, choice=2).status_code == 409
    _answer(client, lid, level="B1", phase="first", index=1, choice=None)            # 分からない
    r = _answer(client, lid, level="B1", phase="first", index=2, choice=0).json()
    les = r["lesson"]
    assert r["done"] and les["first_done"] and len(les["sentences"]) == 6 and les["summary_ja"]
    assert [x["correct"] for x in les["results"]] == [True, False, False]
    assert les["sentences"][0]["audio"].endswith("B1-0.mp3")
    assert {x["lemma"] for x in les["vocabulary"]} == {"announce", "complain", "investment", "wage"}
    assert len(les["transfer"]) == 3 and all(t["audio"] for t in les["transfer"])
    # unseen sentence answered wrong: the word goes to the word book (once)
    t0 = les["transfer"][0]
    wrong = next(i for i in range(4) if i != _secret_answer(lid, 0))
    fb = _answer(client, lid, level="B1", phase="transfer", index=0, choice=wrong).json()
    assert fb["correct"] is False and fb["en"] and fb["added"]["status"] in ("queued", "created")
    assert _answer(client, lid, level="A2", phase="transfer", index=0, choice=wrong).json()["duplicate"], \
        "the unseen sentences are the same at every level: answered once"
    assert db.q1("SELECT origin FROM news_words WHERE lemma=?", (t0["lemma"],))["origin"] == "missed"
    p = client.get("/v1/news/progress").json()
    assert p["first"]["n"] == 3 and p["transfer"]["n"] == 1 and p["first"]["accuracy"] == pytest.approx(1 / 3, abs=1e-3)
    assert client.get("/v1/news").json()["today"][0]["state"] == "listened"


def _secret_answer(lid, i):
    return json.loads(db.q1("SELECT data FROM news_lessons WHERE id=?", (lid,))["data"])["transfer_checks"][i]["answer"]


def test_words_from_the_news_go_to_the_word_book_once_and_are_studied_first(newsenv, client):
    _run()
    lid = client.get("/v1/news").json()["today"][0]["id"]
    for i in range(3):
        _answer(client, lid, level="B1", phase="first", index=i, choice=0)
    lib = vocab_library()
    in_book = [v for v in ("announce", "complain", "investment", "wage") if lib.by_lemma(v)]
    new = [v for v in ("announce", "complain", "investment", "wage") if not lib.by_lemma(v)]
    assert in_book, "the made-up story uses common words"
    a = client.post(f"/v1/news/{lid}/vocabulary", json={"lemma": in_book[0]}).json()
    assert a["status"] == "queued" and a["word_id"] == lib.by_lemma(in_book[0]).id
    assert client.post(f"/v1/news/{lid}/vocabulary", json={"lemma": in_book[0]}).json()["status"] == "already"
    if new:
        b = client.post(f"/v1/news/{lid}/vocabulary", json={"lemma": new[0]}).json()
        assert b["status"] == "created" and b["word_id"] in vocab_library().words
        w = vocab_library().words[b["word_id"]]
        assert w.dataset == "news_words" and w.transfer and w.example_ja
    assert client.post(f"/v1/news/{lid}/vocabulary", json={"lemma": "zzzz"}).status_code == 404
    les = client.get(f"/v1/news/{lid}?level=B1").json()
    assert next(x for x in les["vocabulary"] if x["lemma"] == in_book[0])["added"] is True
    # the next word lesson starts with the word met in the news
    first = client.post("/v1/vocabulary/lessons", json={"minutes": 5}).json()
    item = client.post(f"/v1/vocabulary/lessons/{first['id']}/next").json()["item"]
    assert item["type"] == "word_study" and item["reason"] == "ニュースで出会った単語"
    assert item["lemma"] == in_book[0]


def test_news_routes_need_a_signed_in_device(newsenv, client, monkeypatch):
    monkeypatch.setenv("LISTENFLOW_ACCESS_CODE", "1234-5678-9012")
    monkeypatch.delenv("LISTENFLOW_AUTH", raising=False)
    access.reset_failures()
    for method, path, body in (("get", "/v1/news", None), ("get", "/v1/news/status", None), ("post", "/v1/news/run", {}),
                               ("get", "/v1/news/x", None), ("post", "/v1/news/x/attempt", {}),
                               ("post", "/v1/news/x/vocabulary", {"lemma": "a"}), ("get", "/v1/news/progress", None)):
        r = getattr(client, method)(path, headers=VIA_WEB, **({"json": body} if body is not None else {}))
        assert r.status_code == 401, (path, r.status_code)


# --- every morning -----------------------------------------------------------------------------------

def test_morning_run_is_due_once_a_day_after_the_hour(newsenv, monkeypatch):
    monkeypatch.setenv("LISTENFLOW_NEWS_AUTO", "1")
    hour = news.settings()["hour"]
    today = dt.datetime.now().astimezone()
    assert news.due(today.replace(hour=hour - 1 if hour else 0, minute=0)) is (hour == 0)
    morning = today.replace(hour=hour, minute=5)
    assert news.due(morning)
    db.q("INSERT INTO news_runs (id, date, trigger, started_at, status) VALUES ('a', ?, 'auto', ?, 'failed')",
         (morning.date().isoformat(), morning.isoformat()))
    assert not news.due(morning + dt.timedelta(minutes=10)), "a failed run waits an hour"
    assert news.due(morning + dt.timedelta(minutes=70))
    db.q("INSERT INTO news_runs (id, date, trigger, started_at, status) VALUES ('b', ?, 'auto', ?, 'ok')",
         (morning.date().isoformat(), morning.isoformat()))
    assert not news.due(morning + dt.timedelta(hours=3)), "once a day"
    with db.tx() as conn:
        cur = json.loads(conn.execute("SELECT settings FROM profile WHERE id=1").fetchone()["settings"])
        conn.execute("UPDATE profile SET settings=? WHERE id=1", (json.dumps({**cur, "news_auto": False}),))
    db.q("DELETE FROM news_runs")
    assert not news.due(morning), "switched off in the settings"
    monkeypatch.setattr(news.lessons, "gemini_key", lambda: None)
    assert _run()["status"] == "no_key"


def test_settings_accept_news_fields(client):
    r = client.put("/settings", json={"news_topics": ["technology", "science"], "news_hour": 7, "news_level": "A2"})
    assert r.status_code == 200
    s = news.settings()
    assert s["topics"] == ["technology", "science"] and s["hour"] == 7 and s["level"] == "A2"
    assert client.put("/settings", json={"news_topics": ["sports"]}).status_code == 422


# --- word voices stay put ------------------------------------------------------------------------------

def test_word_voices_do_not_depend_on_which_words_are_rebuilt():
    """Rebuilding one word (or adding news words) must not change the voice of any other word."""
    lib = vocab_library()
    words = list(lib.words.values())[:6]
    alone = {w.id: tts.planned_voice(w, None, "example", {}) for w in words}
    assert alone == {w.id: tts.planned_voice(w, None, "example", {}) for w in reversed(words)}
    for w in words:
        assert tts.planned_voice(w, None, "example", {}) != tts.planned_voice(w, None, "transfer", {})
        # existing audio keeps the voice it was made with, whatever the id would give
        for v in tts.VOCAB_EXAMPLE_VOICES:
            entry = {"hash": tts._vocab_hash(w, v)}
            assert tts.planned_voice(w, entry, "example", {}) == v


def test_money_and_percent_are_read_as_words():
    """`say` reads '£85m' as '85 million British pounds'; the lesson gives it the plain spoken form (and the speech
    check compares with the same form)."""
    assert news.spoken("The £85m plan") == "The 85 million pounds plan"
    assert news.spoken("a $2bn deal, 10% more, a £10,000 prize") == "a 2 billion dollars deal, 10 percent more, a 10,000 pounds prize"
    assert news.spoken("No money here.") == "No money here."
