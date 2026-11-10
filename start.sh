#!/usr/bin/env bash
# ListenFlow: start the API (127.0.0.1:$LISTENFLOW_API_PORT, default 8765) and the web app (0.0.0.0:$LISTENFLOW_WEB_PORT, default 3210) on this Mac.
# Other devices open http://<this-mac>:3210 over Wi-Fi or Tailscale.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
HOST="${LISTENFLOW_HOST:-0.0.0.0}"     # set to your Tailscale IP to accept only Tailscale traffic
PORT="${LISTENFLOW_WEB_PORT:-3210}"
export LISTENFLOW_WEB_PORT="$PORT"
export LISTENFLOW_API_PORT="${LISTENFLOW_API_PORT:-8765}"

# --- backend -------------------------------------------------------------------
cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then
  echo "▶ Python 環境を作成しています…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
.venv/bin/python -c "import multipart" 2>/dev/null || .venv/bin/pip install -q -r requirements.txt
# speech recognition (faster-whisper) lives in its own Python 3.10-3.12 environment
if [ "${1:-}" = "--setup-asr" ] && [ ! -x .venv-asr/bin/python ]; then
  PY=""; for v in python3.12 python3.11 python3.10; do command -v "$v" >/dev/null && PY="$v" && break; done
  if [ -z "$PY" ]; then echo "⚠ 音声認識には Python 3.10〜3.12 が必要です（brew install python@3.12）"; else
    echo "▶ 音声認識（faster-whisper）を導入しています…"
    "$PY" -m venv .venv-asr && .venv-asr/bin/pip install -q -r requirements-asr.txt
  fi
fi
[ -x .venv-asr/bin/python ] || echo "ℹ 音声認識は未導入です（教材の音声照合・NotebookLM 取り込みに必要）: ./start.sh --setup-asr"
if [ ! -f media/manifest.json ] || [ "${1:-}" = "--rebuild-audio" ]; then
  echo "▶ 教材の音声を生成しています（初回のみ・数分）…"
  .venv/bin/python -m scripts.build_audio
fi

# --- frontend ------------------------------------------------------------------
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  echo "▶ npm パッケージをインストールしています…"
  npm ci
fi
if [ ! -f .next/BUILD_ID ] || [ -n "$(find app components lib -newer .next/BUILD_ID -type f 2>/dev/null | head -1)" ]; then
  echo "▶ Web アプリをビルドしています…"
  npm run build
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

cd "$ROOT/backend"
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$LISTENFLOW_API_PORT" --log-level warning &
cd "$ROOT/frontend"
npx next start -H "$HOST" -p "$PORT" &

sleep 2
echo ""
echo "✅ ListenFlow を起動しました"
echo "   この Mac:     http://localhost:$PORT"
curl -s "http://127.0.0.1:$LISTENFLOW_API_PORT/system" | python3 -c 'import json,sys
for u in json.load(sys.stdin)["urls"]: print("   " + u["label"] + ": " + u["url"])' 2>/dev/null || true
echo "   終了するには Ctrl+C"
wait
