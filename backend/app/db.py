"""SQLite storage. All writes go through the API process and are serialized by one lock,
so several devices (Mac / Windows / iPhone) updating at the same time cannot corrupt data."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

WRITE_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  onboarded INTEGER NOT NULL DEFAULT 0,
  level INTEGER NOT NULL DEFAULT 1,
  unlocked INTEGER NOT NULL DEFAULT 1,
  settings TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skill_stats (
  skill TEXT PRIMARY KEY, num REAL NOT NULL DEFAULT 0, den REAL NOT NULL DEFAULT 0, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS level_stats (
  level INTEGER PRIMARY KEY,
  dict_num REAL NOT NULL DEFAULT 0, dict_den REAL NOT NULL DEFAULT 0,
  comp_num REAL NOT NULL DEFAULT 0, comp_den REAL NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS exposures (
  kind TEXT NOT NULL, item_id TEXT NOT NULL, first_seen TEXT, last_seen TEXT,
  count INTEGER NOT NULL DEFAULT 0, best REAL, PRIMARY KEY (kind, item_id)
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, mode TEXT NOT NULL, minutes INTEGER NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
  plan TEXT NOT NULL, state TEXT NOT NULL, test_form TEXT
);
CREATE TABLE IF NOT EXISTS pending_items (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, created_at TEXT NOT NULL,
  payload TEXT NOT NULL, answered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, created_at TEXT NOT NULL, date TEXT NOT NULL,
  kind TEXT NOT NULL, block TEXT, item_id TEXT, level INTEGER, score REAL, detail TEXT
);
CREATE INDEX IF NOT EXISTS attempts_date ON attempts(date);
CREATE INDEX IF NOT EXISTS attempts_session ON attempts(session_id);
CREATE TABLE IF NOT EXISTS srs_cards (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, card TEXT NOT NULL, due TEXT NOT NULL,
  created_at TEXT NOT NULL, reps INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0,
  last_rating INTEGER, suspended INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS srs_due ON srs_cards(due);
CREATE TABLE IF NOT EXISTS daily_log (date TEXT PRIMARY KEY, seconds INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, form TEXT NOT NULL, session_id TEXT, taken_at TEXT NOT NULL,
  day INTEGER, dictation REAL, comprehension REAL, score REAL, level_estimate INTEGER, detail TEXT
);
-- vocabulary: one row per word the learner has met, with separate mastery for
-- reading (text), recognising by ear (audio) and understanding inside a sentence (context)
CREATE TABLE IF NOT EXISTS user_vocab (
  word_id TEXT PRIMARY KEY,
  text_mastery REAL, audio_mastery REAL, context_mastery REAL,
  text_n INTEGER NOT NULL DEFAULT 0, audio_n INTEGER NOT NULL DEFAULT 0, context_n INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT, last_tested_at TEXT, next_review_at TEXT,
  lapsed INTEGER NOT NULL DEFAULT 0      -- was acquired in some modality, then forgotten
);
CREATE TABLE IF NOT EXISTS vocab_diagnostics (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  completed_at TEXT, state TEXT NOT NULL, result TEXT
);
CREATE TABLE IF NOT EXISTS vocab_items (
  id TEXT PRIMARY KEY, origin TEXT NOT NULL, origin_id TEXT, created_at TEXT NOT NULL,
  payload TEXT NOT NULL, answered INTEGER NOT NULL DEFAULT 0, answer TEXT, feedback TEXT
);
CREATE INDEX IF NOT EXISTS vocab_items_origin ON vocab_items(origin, origin_id, answered);
CREATE TABLE IF NOT EXISTS checkpoint_plan (
  slot INTEGER PRIMARY KEY, day INTEGER NOT NULL, form TEXT NOT NULL UNIQUE, assigned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ability_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, date TEXT NOT NULL, session_id TEXT,
  source TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS growth_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL,
  ref TEXT NOT NULL, title TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS imports (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  title TEXT NOT NULL, level INTEGER NOT NULL, source TEXT NOT NULL, info TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS audio_lessons (
  id TEXT PRIMARY KEY, cache_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, engine TEXT NOT NULL,
  targets TEXT NOT NULL, level INTEGER NOT NULL, checks TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sentence_studies (
  id TEXT PRIMARY KEY, session_id TEXT, created_at TEXT NOT NULL, date TEXT NOT NULL, clip_id TEXT NOT NULL,
  context_score REAL, first_listen INTEGER NOT NULL DEFAULT 0, segment_id TEXT, dictation_accuracy REAL,
  replays INTEGER, response_ms INTEGER, text_check TEXT, unknown_words TEXT, meaning_understood INTEGER,
  error_type TEXT NOT NULL DEFAULT 'unknown', transfer_id TEXT, transfer_correct INTEGER, transfer_ms INTEGER,
  mode TEXT NOT NULL DEFAULT 'standard', line_report TEXT
);
-- vocabulary lessons (5-30 min): study a word -> hear it -> hear it in a sentence -> an unseen sentence
CREATE TABLE IF NOT EXISTS vocab_lessons (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, minutes INTEGER NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, completed_at TEXT, state TEXT NOT NULL, result TEXT
);
-- Daily English News: stories found in RSS feeds (headline, summary, link, time), lessons made from them,
-- the runs that made them (with Gemini token use), and the learner's answers (kept apart from `attempts`)
CREATE TABLE IF NOT EXISTS news_sources (
  id TEXT PRIMARY KEY, feed TEXT NOT NULL, publisher TEXT NOT NULL, category TEXT NOT NULL, kind TEXT NOT NULL,
  url TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
  published_at TEXT, fetched_at TEXT NOT NULL, lesson_ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS news_sources_published ON news_sources(published_at);
CREATE TABLE IF NOT EXISTS news_lessons (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, source_id TEXT NOT NULL UNIQUE, category TEXT NOT NULL,
  status TEXT NOT NULL, data TEXT, checks TEXT, audio TEXT, reason TEXT, model TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  -- draft / published / retry (failure: api or audio, after retry_after) / rejected / insufficient
  failure TEXT, retry_after TEXT, calls INTEGER NOT NULL DEFAULT 0, tries INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS news_lessons_date ON news_lessons(date);
CREATE TABLE IF NOT EXISTS news_runs (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, trigger TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS news_answers (
  lesson_id TEXT NOT NULL, level TEXT NOT NULL, phase TEXT NOT NULL, idx INTEGER NOT NULL,
  choice INTEGER, correct INTEGER NOT NULL, ms INTEGER, created_at TEXT NOT NULL, date TEXT NOT NULL,
  PRIMARY KEY (lesson_id, level, phase, idx)
);
-- words met in a news lesson (tapped, or missed in its unseen sentence): studied first in the word lessons
CREATE TABLE IF NOT EXISTS news_words (
  lemma TEXT PRIMARY KEY, word_id TEXT NOT NULL, lesson_id TEXT NOT NULL, origin TEXT NOT NULL, added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, name TEXT NOT NULL, created_at TEXT NOT NULL, last_seen TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "daily_goal": config.DAILY_GOAL_MINUTES,
    "break_enabled": True,
    "break_minutes": 5,
    "break_every": 45,
    "playback_rate": 1.0,
    "show_romaji_hint": False,
    "auto_lessons": True,
    "lesson_engine": "auto",      # auto = Gemini when a key is configured, local otherwise (and on Gemini failure)
    "gemini_model": None,
    "lesson_voice": "mac",        # "gemini": Gemini TTS, one male + one female voice (needs a key)
    "gemini_voice_male": "Algieba",
    "gemini_voice_female": "Aoede",
    "interests": ["tech", "business", "conversation"],   # topics preferred for new listening material         # None = LISTENFLOW_GEMINI_MODEL or the default in lessons.py
    "news_auto": False,            # make today's news lessons every morning (Gemini; needs a key). Off until turned on
    "news_hour": 6,                # from this hour (local time) on; caught up later if the Mac was asleep
    "news_topics": ["technology"],   # technology / business / world / science
    "news_per_day": 3,
    "news_level": "B1",            # the level a news lesson opens with (A2 / B1 / B2)
    "news_levels": ["B1"],         # levels written for each story (only those in news.VERIFIED_LEVELS are made)
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def local_date(dt: datetime | None = None) -> str:
    return (dt or now()).astimezone().date().isoformat()


_conn: sqlite3.Connection | None = None
_conn_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    global _conn
    with _conn_lock:
        if _conn is None:
            config.ensure_dirs()
            _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
            _backup_before_migration(_conn)
            _conn.executescript(SCHEMA)
            # additive migrations for databases created by earlier versions
            cols = {r[1] for r in _conn.execute("PRAGMA table_info(user_vocab)")}
            if "lapsed" not in cols:
                _conn.execute("ALTER TABLE user_vocab ADD COLUMN lapsed INTEGER NOT NULL DEFAULT 0")
            if "correct_days" not in cols:
                # "acquired" now also needs the word right on a second day; count the days already in the log
                _conn.execute("ALTER TABLE user_vocab ADD COLUMN correct_days INTEGER NOT NULL DEFAULT 0")
                _conn.execute("ALTER TABLE user_vocab ADD COLUMN last_correct_date TEXT")
                _conn.execute("UPDATE user_vocab SET correct_days=(SELECT COUNT(DISTINCT date) FROM attempts a"
                              " WHERE a.kind='word' AND a.item_id=user_vocab.word_id AND a.score>=0.8),"
                              " last_correct_date=(SELECT MAX(date) FROM attempts a"
                              " WHERE a.kind='word' AND a.item_id=user_vocab.word_id AND a.score>=0.8)")
            cols = {r[1] for r in _conn.execute("PRAGMA table_info(sentence_studies)")}
            if "mode" not in cols:     # 'standard' (dictation finds the line) or 'commute' (tap: heard / not heard)
                _conn.execute("ALTER TABLE sentence_studies ADD COLUMN mode TEXT NOT NULL DEFAULT 'standard'")
            if "line_report" not in cols:   # commute: understood / sound_only / not_heard for the line studied
                _conn.execute("ALTER TABLE sentence_studies ADD COLUMN line_report TEXT")
            if not _conn.execute("SELECT 1 FROM profile WHERE id=1").fetchone():
                _conn.execute("INSERT INTO profile (id, settings, created_at) VALUES (1, ?, ?)",
                              (json.dumps(DEFAULT_SETTINGS), now_iso()))
        return _conn


MIGRATION_MARKERS = ("vocab_lessons", "news_lessons")   # tables added by the latest schema change


def _backup_before_migration(conn: sqlite3.Connection) -> None:
    """Copy an existing database once before its schema is changed, so learning data can be restored.
    Named outside the daily-backup pattern so the 14-day pruning never removes it."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "profile" not in have or all(t in have for t in MIGRATION_MARKERS):
        return      # a new database, or already migrated
    folder = config.DATA_DIR / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"pre-migration-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
    finally:
        out.close()


def reset_connection() -> None:
    global _conn
    with _conn_lock:
        if _conn is not None:
            _conn.close()
        _conn = None


@contextmanager
def tx():
    """Serialized write transaction."""
    conn = connect()
    with WRITE_LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with WRITE_LOCK:  # sqlite3 connection objects are not safe for concurrent use across threads
        return connect().execute(sql, args).fetchall()


def q1(sql: str, args: tuple = ()) -> sqlite3.Row | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def profile() -> dict:
    r = q1("SELECT * FROM profile WHERE id=1")
    d = dict(r)
    d["settings"] = {**DEFAULT_SETTINGS, **json.loads(d["settings"])}
    return d
