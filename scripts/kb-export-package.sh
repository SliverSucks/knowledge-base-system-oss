#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<USAGE
Usage: $0 [export_dir]

Create a full cross-machine export package (.tar.gz), including:
- PostgreSQL dump
- Qdrant storage
- MinIO storage
USAGE
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

TS="$(date '+%Y%m%d_%H%M%S')"
EXPORT_DIR="${1:-$ROOT_DIR/exports}"
mkdir -p "$EXPORT_DIR"

BACKUP_DIR="$ROOT_DIR/backups/$TS"
"$ROOT_DIR/scripts/backup_create.sh" "$BACKUP_DIR"

OUT="$EXPORT_DIR/kb-export-$TS.tar.gz"
tar -czf "$OUT" -C "$ROOT_DIR/backups" "$TS"

echo "Export package created: $OUT"
