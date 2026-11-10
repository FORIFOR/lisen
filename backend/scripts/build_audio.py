"""Generate audio for all bundled (and user-added) material and the vocabulary database.

    python -m scripts.build_audio            # only new/changed clips and words
    python -m scripts.build_audio --force    # regenerate everything
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tts  # noqa: E402
from app.content import raw_clips  # noqa: E402
from app.vocab_data import vocab_library  # noqa: E402

if __name__ == "__main__":
    ok, msg = tts.available()
    if not ok:
        sys.exit(msg)
    force = "--force" in sys.argv
    tts.build_all([r for r, _ in raw_clips()], force=force)
    tts.build_vocab_audio(list(vocab_library().words.values()), force=force)
    print("done")
