from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

try:
    from scripts.import_markdown import _parse_tags, import_markdown_file
except ModuleNotFoundError:  # pragma: no cover
    from import_markdown import _parse_tags, import_markdown_file


def collect_markdown_files(root: Path, recursive: bool) -> list[Path]:
    patterns: Iterable[str] = ("**/*.md", "**/*.markdown") if recursive else ("*.md", "*.markdown")
    files: list[Path] = []
    for pattern in patterns:
        for p in root.glob(pattern):
            if p.is_file():
                files.append(p.resolve())
    return sorted(set(files))


def should_skip(path: Path) -> bool:
    skip_parts = {".git", ".venv", "node_modules", "__pycache__"}
    return any(part in skip_parts for part in path.parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a markdown directory into knowledge base.")
    parser.add_argument("--dir", required=True, help="Root directory to import")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--domain", default="work", choices=["work", "personal"])
    parser.add_argument("--type", default="fact", choices=["decision", "runbook", "lesson", "fact"])
    parser.add_argument("--module", default="", help="Optional module scope")
    parser.add_argument("--feature", default="", help="Optional feature scope")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--source-uri", default="", help="Source URI for imported docs")
    parser.add_argument("--effective-from", default=None, help="Effective start time (ISO-8601)")
    parser.add_argument("--effective-to", default=None, help="Effective end time (ISO-8601)")
    parser.add_argument("--author", default="batch-markdown-import")
    parser.add_argument("--change-note", default="batch import from markdown directory")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--recursive", action="store_true", default=False)
    parser.add_argument("--max-files", type=int, default=0, help="0 means no limit")
    parser.add_argument("--continue-on-error", action="store_true", default=False)
    args = parser.parse_args()

    root = Path(args.dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise NotADirectoryError(f"Directory not found: {root}")

    candidates = [p for p in collect_markdown_files(root, recursive=args.recursive) if not should_skip(p)]
    if args.max_files > 0:
        candidates = candidates[: args.max_files]

    successes: list[dict] = []
    failures: list[dict] = []

    for p in candidates:
        try:
            result = import_markdown_file(
                file_path=p,
                project=args.project,
                domain=args.domain,
                knowledge_type=args.type,
                module=args.module,
                feature=args.feature,
                tags=_parse_tags(args.tags),
                source_uri=args.source_uri,
                effective_from=args.effective_from,
                effective_to=args.effective_to,
                author=args.author,
                api_url=args.api_url,
                change_note=args.change_note,
            )
            successes.append(
                {
                    "file": result["file"],
                    "knowledge_item_id": result["result"]["knowledge_item_id"],
                    "version": result["result"]["version"],
                }
            )
        except Exception as exc:
            failures.append({"file": str(p), "error": str(exc)})
            if not args.continue_on_error:
                break

    status = "ok" if not failures else "partial"
    print(
        json.dumps(
            {
                "status": status,
                "root": str(root),
                "total_candidates": len(candidates),
                "imported": len(successes),
                "failed": len(failures),
                "successes": successes,
                "failures": failures,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
