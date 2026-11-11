"""Access control for the web app.

Every device (including the Mac's own browser) signs in once with the access code; the API then
recognises it by an HttpOnly cookie that holds a random device token (only its hash is stored).

Requests that reach the API directly on 127.0.0.1 (the API never listens on another interface) come
from processes on this Mac, such as start.sh, and are trusted. Requests relayed by the web server
always carry ``x-forwarded-host`` (Next.js sets it on every proxied request), so they must sign in.
Client-supplied IP headers are not trusted: Next.js keeps an ``x-forwarded-for`` sent by the client.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
import uuid

from . import config, db

COOKIE = "lf_device"
COOKIE_MAX_AGE = 400 * 24 * 3600            # browsers cap cookie lifetime at 400 days
MAX_FAILURES = 5                            # wrong codes allowed per window before sign-in pauses
FAILURE_WINDOW = 600                        # seconds
OPEN_PATHS = {"/auth/login", "/auth/status"}

_fail_lock = threading.Lock()
_failures: list[float] = []


def required() -> bool:
    return os.environ.get("LISTENFLOW_AUTH", "on").lower() not in {"off", "0", "false"}


def code_path():
    return config.DATA_DIR / "secrets" / "access.code"


def _new_code() -> str:
    # 12 digits in groups of four: easy to type on a phone's number pad, 10^12 possibilities
    digits = "".join(secrets.choice("0123456789") for _ in range(12))
    return f"{digits[:4]}-{digits[4:8]}-{digits[8:]}"


def _write_code(code: str) -> None:
    d = code_path().parent
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = code_path().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(code + "\n")
    os.replace(tmp, code_path())


def access_code() -> str:
    env = os.environ.get("LISTENFLOW_ACCESS_CODE")
    if env:
        return env.strip()
    p = code_path()
    if not p.is_file():
        _write_code(_new_code())
    return p.read_text().strip()


def _norm(code: str) -> str:
    return "".join(ch for ch in code if ch.isalnum()).upper()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_direct(headers) -> bool:
    """True for a request made straight to the API (only reachable from this Mac)."""
    return "x-forwarded-host" not in headers


def device_for(token: str | None):
    if not token:
        return None
    return db.q1("SELECT * FROM devices WHERE token_hash=?", (_hash(token),))


def touch(device_id: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE devices SET last_seen=? WHERE id=?", (db.now_iso(), device_id))


class LoginError(Exception):
    def __init__(self, status: int, msg: str):
        self.status, self.msg = status, msg


def _locked(now: float) -> bool:
    with _fail_lock:
        _failures[:] = [t for t in _failures if now - t < FAILURE_WINDOW]
        return len(_failures) >= MAX_FAILURES


def login(code: str, name: str) -> str:
    """Returns a new device token. Wrong codes are limited for all clients together, because a
    client's address cannot be told apart reliably behind the web server."""
    now = time.time()
    if _locked(now):
        raise LoginError(429, "コードの入力に何度も失敗したため、10分ほど待ってからもう一度お試しください")
    if not hmac.compare_digest(_norm(code), _norm(access_code())):
        with _fail_lock:
            _failures.append(now)
        raise LoginError(401, "アクセスコードが違います")
    token = secrets.token_urlsafe(32)
    with db.tx() as c:
        c.execute("INSERT INTO devices (id, token_hash, name, created_at, last_seen) VALUES (?,?,?,?,?)",
                  (uuid.uuid4().hex[:12], _hash(token), (name.strip() or "端末")[:60], db.now_iso(), db.now_iso()))
    return token


def logout(token: str | None) -> None:
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM devices WHERE token_hash=?", (_hash(token),))


def devices(current_id: str | None) -> list[dict]:
    rows = db.q("SELECT id, name, created_at, last_seen FROM devices ORDER BY last_seen DESC")
    return [{**dict(r), "current": r["id"] == current_id} for r in rows]


def revoke(device_id: str) -> bool:
    with db.tx() as c:
        return c.execute("DELETE FROM devices WHERE id=?", (device_id,)).rowcount > 0


def rotate(keep_id: str | None) -> str:
    """New access code; every other signed-in device has to sign in again."""
    if os.environ.get("LISTENFLOW_ACCESS_CODE"):
        raise LoginError(409, "アクセスコードは環境変数 LISTENFLOW_ACCESS_CODE で固定されています")
    code = _new_code()
    _write_code(code)
    with db.tx() as c:
        c.execute("DELETE FROM devices WHERE id IS NOT ?", (keep_id,))
    return code


def reset_failures() -> None:
    with _fail_lock:
        _failures.clear()
