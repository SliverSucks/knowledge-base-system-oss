#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_SRC="$ROOT_DIR/mac-app/KnowledgeBaseMenuBar.app"
APP_DST="/Applications/KnowledgeBaseMenuBar.app"

"$ROOT_DIR/scripts/build_menubar_app.sh"
rm -rf "$APP_DST"
cp -R "$APP_SRC" "$APP_DST"

echo "Installed: $APP_DST"
open "$APP_DST"
