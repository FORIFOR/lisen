"""Device sign-in for requests relayed by the web server, and automatic backups."""
import sqlite3

import pytest

from app import access, backup, db
from scripts import restore_backup

VIA_WEB = {"x-forwarded-host": "192.168.0.10:3210"}


@pytest.fixture()
def code(monkeypatch):
    monkeypatch.setenv("LISTENFLOW_ACCESS_CODE", "1234-5678-9012")
    monkeypatch.delenv("LISTENFLOW_AUTH", raising=False)
    access.reset_failures()
    yield "1234-5678-9012"
    access.reset_failures()


def test_relayed_requests_need_a_signed_in_device(client, code):
    for path in ("/profile", "/export", "/v1/integrations/gemini", "/auth/code", "/audio/x.mp3"):
        r = client.get(path, headers=VIA_WEB)
        assert r.status_code == 401, path
    # a client-supplied address header does not make a relayed request local
    assert client.get("/export", headers={**VIA_WEB, "x-forwarded-for": "127.0.0.1"}).status_code == 401
    # start.sh and other processes on this Mac call the API directly
    assert client.get("/profile").status_code == 200
    st = client.get("/auth/status", headers=VIA_WEB).json()
    assert st == {"required": True, "signed_in": False, "device": None}


def test_sign_in_sign_out_and_revoke(client, code):
    r = client.post("/auth/login", json={"code": "1234 5678 9012", "name": "iPhone"}, headers=VIA_WEB)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    token = client.cookies.get(access.COOKIE)
    assert token and db.q1("SELECT * FROM devices WHERE token_hash=?", (token,)) is None   # only the hash is stored
    assert client.get("/profile", headers=VIA_WEB).status_code == 200
    devs = client.get("/auth/devices", headers=VIA_WEB).json()["devices"]
    assert [(d["name"], d["current"]) for d in devs] == [("iPhone", True)]
    assert client.get("/auth/code", headers=VIA_WEB).json()["code"] == code
    client.post("/auth/logout", headers=VIA_WEB)
    client.cookies.set(access.COOKIE, token)                     # the old token no longer works
    assert client.get("/profile", headers=VIA_WEB).status_code == 401
    # revoking from another device
    client.cookies.clear()
    client.post("/auth/login", json={"code": code, "name": "Windows"}, headers=VIA_WEB)
    win = client.cookies.get(access.COOKIE)
    client.cookies.clear()
    client.post("/auth/login", json={"code": code, "name": "Mac"}, headers=VIA_WEB)
    wid = next(d["id"] for d in client.get("/auth/devices", headers=VIA_WEB).json()["devices"] if d["name"] == "Windows")
    assert client.delete(f"/auth/devices/{wid}", headers=VIA_WEB).status_code == 200
    client.cookies.set(access.COOKIE, win)
    assert client.get("/profile", headers=VIA_WEB).status_code == 401


def test_wrong_codes_pause_sign_in(client, code):
    for _ in range(access.MAX_FAILURES):
        assert client.post("/auth/login", json={"code": "0000-0000-0000"}, headers=VIA_WEB).status_code == 401
    # even the right code waits once the limit is reached
    assert client.post("/auth/login", json={"code": code}, headers=VIA_WEB).status_code == 429


def test_rotating_the_code_signs_out_other_devices(client, monkeypatch, tmp_path):
    monkeypatch.delenv("LISTENFLOW_ACCESS_CODE", raising=False)
    access.reset_failures()
    first = access.access_code()
    assert (access.code_path().stat().st_mode & 0o777) == 0o600
    client.post("/auth/login", json={"code": first, "name": "Old"}, headers=VIA_WEB)
    old = client.cookies.get(access.COOKIE)
    client.cookies.clear()
    client.post("/auth/login", json={"code": first, "name": "Mac"}, headers=VIA_WEB)
    new = client.post("/auth/code/rotate", headers=VIA_WEB).json()["code"]
    assert new != first and access.access_code() == new
    assert client.get("/profile", headers=VIA_WEB).status_code == 200      # the device that rotated stays in
    client.cookies.set(access.COOKIE, old)
    assert client.get("/profile", headers=VIA_WEB).status_code == 401
    assert client.post("/auth/login", json={"code": first}, headers=VIA_WEB).status_code == 401


def test_auth_can_be_turned_off(client, monkeypatch):
    monkeypatch.setenv("LISTENFLOW_AUTH", "off")
    assert client.get("/profile", headers=VIA_WEB).status_code == 200


def test_backup_is_consistent_pruned_and_restorable(client, monkeypatch):
    client.post("/onboarding/skip", json={"level": 3})
    b = backup.backup_now()
    path = backup.backup_dir() / b["name"]
    con = sqlite3.connect(path)
    assert con.execute("SELECT level FROM profile").fetchone()[0] == 3
    con.close()
    for i in range(backup.KEEP + 3):
        backup.backup_now(tag=f"t{i:02d}")
    assert len(backup.list_backups()) == backup.KEEP
    assert client.get("/backups").json()["keep"] == backup.KEEP
    # restore: the learner's data comes back, the replaced copy is kept
    keep_name = backup.list_backups()[0]["name"]
    with db.tx() as c:
        c.execute("UPDATE profile SET level=1")
    db.reset_connection()
    monkeypatch.setattr(restore_backup, "_api_running", lambda: False)
    assert restore_backup.main(["x", keep_name]) == 0
    assert db.q1("SELECT level FROM profile")["level"] == 3
    assert list(db.config.DB_PATH.parent.glob("*.before-restore-*"))
    assert restore_backup.main(["x", "../t.db"]) == 1                     # only files in data/backups
