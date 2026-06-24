#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$ROOT_DIR/mac-app/KnowledgeBaseMenuBar.app"

if [[ ! -d "$APP_DIR" ]]; then
  "$ROOT_DIR/scripts/build_menubar_app.sh"
fi

open "$APP_DIR"

echo "Opened: $APP_DIR"
