"""Restore the learning database from data/backups.

Stop ListenFlow (Ctrl+C in the start.sh terminal) first, then:

    cd backend && .venv/bin/python -m scripts.restore_backup            # list backups
    cd backend && .venv/bin/python -m scripts.restore_backup <file>     # restore one

The current database is kept as ``listenflow.db.before-restore-<time>`` so a restore can be undone.
"""
from __future__ import annotations

import datetime as dt
import shutil
import socket
import sqlite3
import sys

from app import backup, config


def _api_running() -> bool:
    import os

    port = int(os.environ.get("LISTENFLOW_API_PORT", "8765"))
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main(argv: list[str]) -> int:
    items = backup.list_backups()
    if len(argv) < 2:
        if not items:
            print("バックアップはまだありません")
        for b in items:
            print(f"{b['name']}  {b['created_at']}  {b['bytes'] // 1024} KB")
        return 0
    src = backup.backup_dir() / argv[1]
    if not src.is_file() or src.parent != backup.backup_dir():
        print(f"見つかりません: {argv[1]}")
        return 1
    if _api_running():
        print("ListenFlow が起動中です。先に終了してから復元してください")
        return 1
    con = sqlite3.connect(src)
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    if ok != "ok":
        print(f"このバックアップは壊れています: {ok}")
        return 1
    dbp = config.DB_PATH
    if dbp.exists():
        cur = sqlite3.connect(dbp)                        # fold the WAL into the file before copying it
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cur.close()
        keep = dbp.with_name(f"{dbp.name}.before-restore-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(dbp, keep)
        print(f"今のデータを保存しました: {keep}")
    for suffix in ("-wal", "-shm"):                       # stale WAL files would be replayed onto the restored copy
        p = dbp.with_name(dbp.name + suffix)
        if p.exists():
            p.unlink()
    shutil.copy2(src, dbp)
    print(f"復元しました: {src.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
