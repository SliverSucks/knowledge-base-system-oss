#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

TS="$(date '+%Y%m%d_%H%M%S')"
BACKUP_DIR="${1:-$ROOT_DIR/backups/$TS}"

detect_backend() {
  if [[ -n "${KB_BACKEND:-}" ]]; then
    echo "$KB_BACKEND"
    return
  fi
  if [[ -f "$ROOT_DIR/data/knowledge.db" || -d "$ROOT_DIR/data/qdrant_local" ]]; then
    echo "sqlite"
    return
  fi
  echo "postgres"
}

copy_if_exists() {
  local src="$1"
  local dst="$2"
  if [[ -e "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -R "$src" "$dst"
  fi
}

BACKEND="$(detect_backend)"
mkdir -p "$BACKUP_DIR/config" "$BACKUP_DIR/data" "$BACKUP_DIR/meta"

copy_if_exists "$ROOT_DIR/config/config.toml" "$BACKUP_DIR/config/config.toml"

if [[ "$BACKEND" == "sqlite" ]]; then
  copy_if_exists "$ROOT_DIR/data/knowledge.db" "$BACKUP_DIR/data/knowledge.db"
  copy_if_exists "$ROOT_DIR/data/qdrant_local" "$BACKUP_DIR/data/qdrant_local"
  copy_if_exists "$ROOT_DIR/data/import_state.json" "$BACKUP_DIR/data/import_state.json"
else
  copy_if_exists "$ROOT_DIR/data/postgres" "$BACKUP_DIR/data/postgres"
  copy_if_exists "$ROOT_DIR/data/qdrant" "$BACKUP_DIR/data/qdrant"
  copy_if_exists "$ROOT_DIR/data/minio" "$BACKUP_DIR/data/minio"
fi

cat > "$BACKUP_DIR/meta/manifest.json" <<EOF
{
  "created_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "backend": "$BACKEND",
  "host": "$(hostname)"
}
EOF

echo "Backup created: $BACKUP_DIR"
