from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.import_document import build_payload, extract_text
    from scripts.import_markdown import _parse_tags, upsert_payload
except ModuleNotFoundError:  # pragma: no cover
    from import_document import build_payload, extract_text  # type: ignore
    from import_markdown import _parse_tags, upsert_payload  # type: ignore


SKIP_PARTS = {".git", ".venv", "node_modules", "__pycache__"}
DEFAULT_PATTERNS = ("*.md", "*.markdown", "*.txt", "*.pdf", "*.docx")


@dataclass
class FileState:
    sha256: str
    knowledge_item_id: str
    version: int
    title: str
    imported_at: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def should_skip(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def collect_files(root: Path, recursive: bool, patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        iterator = root.rglob(pattern) if recursive else root.glob(pattern)
        for p in iterator:
            if p.is_file() and not should_skip(p):
                files.append(p.resolve())
    return sorted(set(files))


def load_state(path: Path) -> dict[str, FileState]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    files = raw.get("files", {}) if isinstance(raw, dict) else {}
    state: dict[str, FileState] = {}
    for k, v in files.items():
        if not isinstance(v, dict):
            continue
        try:
            state[k] = FileState(
                sha256=str(v.get("sha256", "")),
                knowledge_item_id=str(v.get("knowledge_item_id", "")),
                version=int(v.get("version", 0)),
                title=str(v.get("title", "")),
                imported_at=str(v.get("imported_at", "")),
            )
        except Exception:
            continue
    return state


def save_state(path: Path, files: dict[str, FileState]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "files": {
            k: {
                "sha256": v.sha256,
                "knowledge_item_id": v.knowledge_item_id,
                "version": v.version,
                "title": v.title,
                "imported_at": v.imported_at,
            }
            for k, v in sorted(files.items())
        },
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_backup(root_dir: Path, backup_dir: str | None) -> str:
    cmd = [str(root_dir / "scripts" / "backup_create.sh")]
    if backup_dir:
        cmd.append(backup_dir)
    proc = subprocess.run(cmd, cwd=str(root_dir), check=True, capture_output=True, text=True)
    output = (proc.stdout or "").strip().splitlines()
    for line in reversed(output):
        if line.startswith("Backup created:"):
            return line.split(":", 1)[1].strip()
    return backup_dir or ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Incremental document import with pre-import backup.")
    parser.add_argument("--dir", required=True, help="Directory to import")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--domain", default="work", choices=["work", "personal"])
    parser.add_argument("--type", default="fact", choices=["decision", "runbook", "lesson", "fact"])
    parser.add_argument("--module", default="", help="Module scope")
    parser.add_argument("--feature", default="", help="Feature scope")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--source-uri", default="", help="Source URI (default file://path)")
    parser.add_argument("--effective-from", default=None, help="Effective start (ISO-8601)")
    parser.add_argument("--effective-to", default=None, help="Effective end (ISO-8601)")
    parser.add_argument("--author", default="incremental-import")
    parser.add_argument("--change-note", default="incremental import")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--recursive", action="store_true", default=False)
    parser.add_argument(
        "--patterns",
        default=",".join(DEFAULT_PATTERNS),
        help="Comma-separated glob patterns, e.g. *.md,*.pdf",
    )
    parser.add_argument("--max-files", type=int, default=0, help="0 means no limit")
    parser.add_argument("--state-file", default="data/import_state.json", help="State json path")
    parser.add_argument("--backup-dir", default=None, help="Optional backup directory")
    parser.add_argument("--no-backup", action="store_true", default=False, help="Skip backup (not recommended)")
    parser.add_argument("--continue-on-error", action="store_true", default=False)
    parser.add_argument("--enable-ocr", action="store_true", default=False)
    parser.add_argument("--ocr-lang", default="eng")
    parser.add_argument("--ocr-max-pages", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    input_dir = Path(args.dir).expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise NotADirectoryError(f"Directory not found: {input_dir}")

    patterns = [p.strip() for p in args.patterns.split(",") if p.strip()]
    if not patterns:
        patterns = list(DEFAULT_PATTERNS)

    candidates = collect_files(input_dir, recursive=args.recursive, patterns=patterns)
    if args.max_files > 0:
        candidates = candidates[: args.max_files]

    state_path = Path(args.state_file)
    if not state_path.is_absolute():
        state_path = root / state_path
    state = load_state(state_path)

    changed: list[Path] = []
    skipped: list[dict[str, Any]] = []
    for p in candidates:
        digest = sha256_file(p)
        current = state.get(str(p))
        if current and current.sha256 == digest:
            skipped.append({"file": str(p), "reason": "unchanged"})
            continue
        changed.append(p)

    backup_path = ""
    if changed and not args.no_backup and not args.dry_run:
        backup_path = run_backup(root, args.backup_dir)

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    tags = _parse_tags(args.tags)

    for p in changed:
        digest = sha256_file(p)
        prev = state.get(str(p))
        if args.dry_run:
            successes.append({"file": str(p), "action": "would-import", "previous_item": prev.knowledge_item_id if prev else ""})
            continue

        payload_args = argparse.Namespace(
            knowledge_item_id=(prev.knowledge_item_id if prev and prev.knowledge_item_id else None),
            title=None,
            summary=None,
            domain=args.domain,
            project=args.project,
            module=args.module,
            feature=args.feature,
            tags=tags,
            source_uri=args.source_uri or f"file://{p}",
            effective_from=args.effective_from,
            effective_to=args.effective_to,
            type=args.type,
            author=args.author,
            change_note=args.change_note,
        )
        try:
            content = extract_text(
                p,
                enable_ocr=args.enable_ocr,
                ocr_lang=args.ocr_lang,
                ocr_max_pages=args.ocr_max_pages,
            )
            if not content.strip():
                raise ValueError("document has no extractable text")
            payload = build_payload(payload_args, p, content)
            upsert = upsert_payload(payload, args.api_url)["result"]
            item_id = str(upsert["knowledge_item_id"])
            version = int(upsert["version"])
            imported_at = datetime.now(timezone.utc).isoformat()
            state[str(p)] = FileState(
                sha256=digest,
                knowledge_item_id=item_id,
                version=version,
                title=str(payload.get("title", "")),
                imported_at=imported_at,
            )
            successes.append(
                {
                    "file": str(p),
                    "knowledge_item_id": item_id,
                    "version": version,
                    "updated_existing": bool(prev and prev.knowledge_item_id),
                }
            )
        except Exception as exc:
            failures.append({"file": str(p), "error": str(exc)})
            if not args.continue_on_error:
                break

    if not args.dry_run:
        save_state(state_path, state)

    status = "ok" if not failures else "partial"
    summary = {
        "status": status,
        "root": str(input_dir),
        "total_candidates": len(candidates),
        "changed_files": len(changed),
        "imported": len(successes),
        "skipped": len(skipped),
        "failed": len(failures),
        "backup_dir": backup_path,
        "dry_run": args.dry_run,
        "successes": successes,
        "skipped_items": skipped,
        "failures": failures,
        "state_file": str(state_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
