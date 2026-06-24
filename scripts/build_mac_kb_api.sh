#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

VENV_PY="$ROOT_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "missing venv python: $VENV_PY" >&2
  exit 1
fi

if ! "$VENV_PY" -m PyInstaller --version >/dev/null 2>&1; then
  echo "PyInstaller not available in .venv. run: .venv/bin/pip install pyinstaller" >&2
  exit 1
fi

mkdir -p "$ROOT_DIR/bin" "$ROOT_DIR/build"

"$VENV_PY" -m PyInstaller \
  --onefile \
  --name kb-api \
  --distpath "$ROOT_DIR/bin" \
  --workpath "$ROOT_DIR/build/api-mac" \
  --specpath "$ROOT_DIR/build" \
  --collect-all app \
  --collect-all qdrant_client \
  --collect-all grpc \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import uvicorn.protocols.http.h11_impl \
  --hidden-import uvicorn.protocols.websockets.websockets_impl \
  --hidden-import uvicorn.logging \
  --hidden-import h11 \
  --hidden-import anyio \
  --hidden-import starlette \
  --add-data "$ROOT_DIR/app/static:app/static" \
  "$ROOT_DIR/app/server_entry.py"

if [[ ! -x "$ROOT_DIR/bin/kb-api" ]]; then
  echo "build failed: bin/kb-api not generated" >&2
  exit 1
fi

echo "Built: $ROOT_DIR/bin/kb-api"
