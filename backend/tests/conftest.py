import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="listenflow-test-")
os.environ["LISTENFLOW_DATA_DIR"] = _tmp
os.environ["LISTENFLOW_DB"] = str(Path(_tmp) / "test.db")
os.environ["LISTENFLOW_AUTO_LESSONS"] = "0"  # background lesson generation is tested directly
# never call the real Gemini API from tests, whatever the shell exports
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("LISTENFLOW_GEMINI_MODEL", None)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, db
    from app.content import reload_library
    from app.main import app

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USER_CONTENT_DIR", tmp_path / "user_content")
    db.reset_connection()
    reload_library()
    yield TestClient(app)
    db.reset_connection()
