#!/usr/bin/env bash
# Chạy Sale-manager trên Linux / macOS (máy để bàn hoặc thử trên VPS). Dùng cho máy chủ thật: deploy/linux/install.sh
set -euo pipefail
cd "$(dirname "$0")/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip >/dev/null
  .venv/bin/pip install -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
HOST="${SALE_HOST:-127.0.0.1}"
PORT="${SALE_PORT:-8000}"
echo "Sale-manager: http://${HOST}:${PORT}  (Ctrl+C để tắt)"
exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" --proxy-headers
