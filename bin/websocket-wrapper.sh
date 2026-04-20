#!/bin/sh
set -e

export PYTHONPATH="$SNAP/app"
export APP_PREFIX="${APP_PREFIX:-/api-sysid}"
export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8000}"

exec "$SNAP/venv/bin/python" -m uvicorn main:app \
  --app-dir "$SNAP/app" \
  --host "$API_HOST" \
  --port "$API_PORT"