#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT_DIR/data/.local_api.pid"
source "$ROOT_DIR/scripts/kb-ports.sh"

stop_pid() {
  local pid="$1"
  if [[ -z "$pid" ]] || ! [[ "$pid" =~ ^[0-9]+$ ]]; then
    return
  fi
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return
  fi
  kill "$pid" >/dev/null 2>&1 || true
  for _ in {1..15}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return
    fi
    sleep 0.2
  done
  kill -9 "$pid" >/dev/null 2>&1 || true
}

PORT="${KB_PORT_API:-18000}"

stopped=0

if [[ -f "$PID_FILE" ]]; then
  pid="$(tr -d '[:space:]' < "$PID_FILE" || true)"
  if [[ -n "$pid" ]]; then
    stop_pid "$pid"
    stopped=1
  fi
  rm -f "$PID_FILE"
fi

if command -v lsof >/dev/null 2>&1; then
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    stop_pid "$pid"
    stopped=1
  done < <(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
fi

if command -v pgrep >/dev/null 2>&1; then
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    stop_pid "$pid"
    stopped=1
  done < <(pgrep -f "$ROOT_DIR/bin/kb-api" 2>/dev/null || true)

  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    stop_pid "$pid"
    stopped=1
  done < <(pgrep -f "uvicorn app.main:app" 2>/dev/null || true)
fi

if [[ "$stopped" -eq 1 ]]; then
  echo "Knowledge base stopped (port=$PORT)"
  exit 0
fi

if [[ -f "$ROOT_DIR/docker-compose.yml" || -f "$ROOT_DIR/docker-compose.yaml" ]]; then
  if command -v docker >/dev/null 2>&1; then
    docker compose stop api postgres qdrant prometheus grafana >/dev/null
    echo "Knowledge base docker services stopped"
    exit 0
  fi
fi

echo "Knowledge base not running (port=$PORT)"
exit 0
