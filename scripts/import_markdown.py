from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

@dataclass
class _Enriched:
    summary: str
    tags: list[str]
    mode: str


try:
    from scripts.enrichment import enrich_metadata as _external_enrich_metadata
except ModuleNotFoundError:  # pragma: no cover
    try:
        from enrichment import enrich_metadata as _external_enrich_metadata  # type: ignore
    except ModuleNotFoundError:  # pragma: no cover
        _external_enrich_metadata = None


def enrich_metadata(title: str, content: str, summary: str | None = None) -> _Enriched:
    if _external_enrich_metadata is not None:
        return _external_enrich_metadata(title=title, content=content, summary=summary)
    return _Enriched(summary=summary or infer_summary(content), tags=[], mode="fallback")


def infer_title(path: Path, content: str) -> str:
    for line in content.splitlines():
        striped = line.strip()
        if striped.startswith("# "):
            return striped[2:].strip()
    return path.stem


def infer_summary(content: str) -> str:
    for line in content.splitlines():
        striped = line.strip()
        if not striped or striped.startswith("#"):
            continue
        return striped[:120]
    return "Imported from markdown"


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        tag = part.strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def build_payload(args: argparse.Namespace, content: str, title: str) -> dict[str, Any]:
    return {
        "knowledge_item_id": args.knowledge_item_id,
        "title": title,
        "domain": args.domain,
        "project": args.project,
        "module": getattr(args, "module", ""),
        "feature": getattr(args, "feature", ""),
        "tags": getattr(args, "tags", []),
        "source_uri": getattr(args, "source_uri", ""),
        "effective_from": getattr(args, "effective_from", None),
        "effective_to": getattr(args, "effective_to", None),
        "type": args.type,
        "content_markdown": content,
        "summary": args.summary or infer_summary(content),
        "author": args.author,
        "change_note": args.change_note,
    }


def upsert_payload(payload: dict[str, Any], api_url: str) -> dict[str, Any]:
    endpoint = f"{api_url.rstrip('/')}/v1/knowledge/items/upsert"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(endpoint, json=payload)
        resp.raise_for_status()
        body = resp.json()
    return {"endpoint": endpoint, "result": body}


def import_markdown_file(
    *,
    file_path: Path,
    project: str,
    domain: str,
    knowledge_type: str,
    author: str,
    api_url: str,
    change_note: str,
    title: str | None = None,
    summary: str | None = None,
    knowledge_item_id: str | None = None,
    module: str = "",
    feature: str = "",
    tags: list[str] | None = None,
    source_uri: str = "",
    effective_from: str | None = None,
    effective_to: str | None = None,
) -> dict[str, Any]:
    content = file_path.read_text(encoding="utf-8")
    resolved_title = title or infer_title(file_path, content)
    enriched = enrich_metadata(title=resolved_title, content=content, summary=summary)
    enriched_change_note = change_note
    merged_tags = _parse_tags(",".join(tags or []))
    for t in enriched.tags:
        if t not in merged_tags:
            merged_tags.append(t)
    if merged_tags:
        tag_line = "tags=" + ",".join(merged_tags)
        enriched_change_note = f"{change_note}; {tag_line}"
    args = argparse.Namespace(
        knowledge_item_id=knowledge_item_id,
        domain=domain,
        project=project,
        module=module,
        feature=feature,
        tags=merged_tags,
        source_uri=source_uri,
        effective_from=effective_from,
        effective_to=effective_to,
        type=knowledge_type,
        summary=enriched.summary,
        author=author,
        change_note=enriched_change_note,
    )
    payload = build_payload(args, content, resolved_title)
    upsert = upsert_payload(payload, api_url)
    return {
        "file": str(file_path),
        "title": resolved_title,
        "summary": payload["summary"],
        "tags": merged_tags,
        "enrichment_mode": enriched.mode,
        "endpoint": upsert["endpoint"],
        "result": upsert["result"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a markdown file into knowledge base.")
    parser.add_argument("--file", required=True, help="Path to markdown file")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--domain", default="work", choices=["work", "personal"])
    parser.add_argument("--type", default="fact", choices=["decision", "runbook", "lesson", "fact"])
    parser.add_argument("--title", default=None, help="Optional explicit title")
    parser.add_argument("--summary", default=None, help="Optional explicit summary")
    parser.add_argument("--module", default="", help="Optional module scope")
    parser.add_argument("--feature", default="", help="Optional feature scope")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--source-uri", default="", help="Source URI for this knowledge")
    parser.add_argument("--effective-from", default=None, help="Effective start time (ISO-8601)")
    parser.add_argument("--effective-to", default=None, help="Effective end time (ISO-8601)")
    parser.add_argument("--author", default="markdown-import")
    parser.add_argument("--change-note", default="import from markdown")
    parser.add_argument("--knowledge-item-id", default=None, help="If set, append new version to existing item")
    parser.add_argument("--api-url", default="http://localhost:8000")
    args = parser.parse_args()

    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    imported = import_markdown_file(
        file_path=file_path,
        project=args.project,
        domain=args.domain,
        knowledge_type=args.type,
        author=args.author,
        api_url=args.api_url,
        change_note=args.change_note,
        title=args.title,
        summary=args.summary,
        knowledge_item_id=args.knowledge_item_id,
        module=args.module,
        feature=args.feature,
        tags=_parse_tags(args.tags),
        source_uri=args.source_uri,
        effective_from=args.effective_from,
        effective_to=args.effective_to,
    )
    print(json.dumps({"status": "ok", **imported}, ensure_ascii=False))


if __name__ == "__main__":
    main()
