#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT_DIR/data/.local_api.pid"
SHORT=0
source "$ROOT_DIR/scripts/kb-ports.sh"

if [[ "${1:-}" == "--short" ]]; then
  SHORT=1
fi

PORT="${KB_PORT_API:-18000}"

if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  if [[ "$SHORT" -eq 1 ]]; then
    echo "running"
  else
    echo "running (port=${PORT})"
  fi
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  pid="$(tr -d '[:space:]' < "$PID_FILE" || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    if [[ "$SHORT" -eq 1 ]]; then
      echo "running"
    else
      echo "running (pid=${pid}, health=unknown, port=${PORT})"
    fi
    exit 0
  fi
fi

if [[ "$SHORT" -eq 1 ]]; then
  echo "stopped"
else
  echo "stopped (port=${PORT})"
fi
exit 1
