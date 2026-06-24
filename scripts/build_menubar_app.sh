#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE_APP="$ROOT_DIR/mac-app/KnowledgeBaseMenuBar.app"
ASSETS_DIR="$ROOT_DIR/mac-app/assets"

OUTPUT_APP="$TEMPLATE_APP"
PROJECT_ROOT="$ROOT_DIR"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT_APP="${2:-}"
      shift 2
      ;;
    --project-root)
      PROJECT_ROOT="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat <<USAGE
Usage: $0 [--output <app_path>] [--project-root <runtime_root>]

Prepare KnowledgeBaseMenuBar.app:
- copy from template bundle
- refresh icon/menu assets
- write project_root.txt for runtime command resolution
USAGE
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "$TEMPLATE_APP" ]]; then
  echo "template app not found: $TEMPLATE_APP" >&2
  exit 1
fi

if [[ "$OUTPUT_APP" != "$TEMPLATE_APP" ]]; then
  rm -rf "$OUTPUT_APP"
  mkdir -p "$(dirname "$OUTPUT_APP")"
  cp -R "$TEMPLATE_APP" "$OUTPUT_APP"
fi

RES_DIR="$OUTPUT_APP/Contents/Resources"
BIN_PATH="$OUTPUT_APP/Contents/MacOS/KnowledgeBaseMenuBar"

if [[ ! -x "$BIN_PATH" ]]; then
  echo "menubar executable not found: $BIN_PATH" >&2
  exit 1
fi

mkdir -p "$RES_DIR"

for name in KnowledgeBaseMenuBar.icns menu-running-64.png menu-stopped-64.png menu-busy-64.png; do
  if [[ -f "$ASSETS_DIR/$name" ]]; then
    cp "$ASSETS_DIR/$name" "$RES_DIR/$name"
  fi
done

printf '%s\n' "$PROJECT_ROOT" > "$RES_DIR/project_root.txt"

echo "Prepared menubar app: $OUTPUT_APP"
echo "Runtime project root: $PROJECT_ROOT"
