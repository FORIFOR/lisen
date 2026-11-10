from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
CONTENT_DIR = Path(os.environ.get("LISTENFLOW_CONTENT_DIR", BACKEND_DIR / "content"))
DATA_DIR = Path(os.environ.get("LISTENFLOW_DATA_DIR", BACKEND_DIR.parent / "data"))
AUDIO_DIR = Path(os.environ.get("LISTENFLOW_AUDIO_DIR", BACKEND_DIR / "media"))
USER_CONTENT_DIR = DATA_DIR / "user_content"
DB_PATH = Path(os.environ.get("LISTENFLOW_DB", DATA_DIR / "listenflow.db"))

# words per minute used for TTS by level (slower for beginners)
LEVEL_RATE = {1: 150, 2: 165, 3: 178, 4: 190, 5: 200}
DEFAULT_VOICE = "Samantha"
DAILY_GOAL_MINUTES = 30
CURRICULUM_DAYS = 60


def ensure_dirs() -> None:
    for d in (DATA_DIR, AUDIO_DIR, USER_CONTENT_DIR):
        d.mkdir(parents=True, exist_ok=True)
