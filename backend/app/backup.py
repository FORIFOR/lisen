"""Automatic backups of the learning database.

A consistent copy is taken with SQLite's online backup (safe while the app is in use) when the API
starts and then once a day, into ``data/backups``. The newest ``KEEP`` copies are kept.
Restore with ``python -m scripts.restore_backup`` while ListenFlow is stopped.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import threading
from pathlib import Path

from . import config, db

KEEP = 14
PREFIX = "listenflow-"
log = logging.getLogger("listenflow")
_lock = threading.Lock()
_started = False


def backup_dir() -> Path:
    return config.DATA_DIR / "backups"


def list_backups() -> list[dict]:
    d = backup_dir()
    if not d.is_dir():
        return []
    files = sorted(d.glob(f"{PREFIX}*.db"), reverse=True)
    return [{"name": f.name, "bytes": f.stat().st_size,
             "created_at": dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")} for f in files]


def _check(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise RuntimeError(f"backup failed integrity check: {ok}")
        con.execute("SELECT COUNT(*) FROM profile").fetchone()
    finally:
        con.close()


def backup_now(tag: str | None = None) -> dict:
    """Write today's backup (replacing an earlier one from the same day) and prune old ones."""
    with _lock:
        d = backup_dir()
        d.mkdir(parents=True, exist_ok=True)
        name = f"{PREFIX}{dt.datetime.now().strftime('%Y-%m-%d')}{'-' + tag if tag else ''}.db"
        tmp = d / (name + ".tmp")
        src = db.connect()
        dst = sqlite3.connect(tmp)
        try:
            with db.WRITE_LOCK:
                src.backup(dst)
        finally:
            dst.close()
        _check(tmp)
        tmp.replace(d / name)
        for old in sorted(d.glob(f"{PREFIX}*.db"), reverse=True)[KEEP:]:
            old.unlink(missing_ok=True)
        return next(b for b in list_backups() if b["name"] == name)


def start_daily() -> None:
    """Back up now, then every 24 hours, in a background thread (once per process)."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        stop = threading.Event()
        while True:
            try:
                backup_now()
            except Exception as e:  # noqa: BLE001
                log.warning("automatic backup failed: %s", e)
            stop.wait(24 * 3600)

    threading.Thread(target=loop, daemon=True, name="listenflow-backup").start()
