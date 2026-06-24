from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from scripts.import_markdown import _parse_tags, infer_summary, infer_title, upsert_payload
except ModuleNotFoundError:  # pragma: no cover
    from import_markdown import _parse_tags, infer_summary, infer_title, upsert_payload
@dataclass
class _Enriched:
    summary: str
    tags: list[str]


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
    # Fallback: keep imports usable even without optional enrichment module.
    return _Enriched(summary=summary or infer_summary(content), tags=[])


try:
    from scripts.ocr_extract import ocr_image, ocr_pdf
except ModuleNotFoundError:  # pragma: no cover
    try:
        from ocr_extract import ocr_image, ocr_pdf  # type: ignore
    except ModuleNotFoundError:  # pragma: no cover
        def ocr_image(path: Path, lang: str = "eng") -> str:  # type: ignore
            raise RuntimeError("OCR module not available")

        def ocr_pdf(path: Path, lang: str = "eng", max_pages: int = 20) -> str:  # type: ignore
            raise RuntimeError("OCR module not available")


def extract_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_docx(path: Path) -> str:
    try:
        from docx import Document  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("python-docx is required for .docx import") from exc
    doc = Document(str(path))
    texts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(texts)


def _load_pdf_reader():
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("pypdf is required for .pdf import") from exc
    return PdfReader


def extract_pdf(path: Path, *, enable_ocr: bool = False, ocr_lang: str = "eng", ocr_max_pages: int = 20) -> str:
    reader_cls = _load_pdf_reader()
    reader = reader_cls(str(path))
    texts: list[str] = []
    for page in reader.pages:
        t = page.extract_text() or ""
        t = t.strip()
        if t:
            texts.append(t)
    plain = "\n\n".join(texts).strip()
    if plain:
        return plain
    if enable_ocr:
        return ocr_pdf(path, lang=ocr_lang, max_pages=ocr_max_pages)
    return plain


def extract_text(
    path: Path, *, enable_ocr: bool = False, ocr_lang: str = "eng", ocr_max_pages: int = 20
) -> str:
    suffix = path.suffix.lower()
    if suffix in (".md", ".markdown", ".txt"):
        return extract_markdown(path)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".pdf":
        return extract_pdf(path, enable_ocr=enable_ocr, ocr_lang=ocr_lang, ocr_max_pages=ocr_max_pages)
    if suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"):
        if not enable_ocr:
            raise ValueError("Image import requires --enable-ocr")
        return ocr_image(path, lang=ocr_lang)
    raise ValueError(f"Unsupported file type: {suffix}")


def build_payload(args: argparse.Namespace, file_path: Path, content: str) -> dict[str, Any]:
    title = args.title or infer_title(file_path, content)
    enriched = enrich_metadata(title=title, content=content, summary=args.summary)
    merged_tags = _parse_tags(",".join(getattr(args, "tags", []) or []))
    for t in enriched.tags:
        if t not in merged_tags:
            merged_tags.append(t)
    change_note = args.change_note
    if merged_tags:
        change_note = f"{change_note}; tags={','.join(merged_tags)}"
    return {
        "knowledge_item_id": args.knowledge_item_id,
        "title": title,
        "domain": args.domain,
        "project": args.project,
        "module": getattr(args, "module", ""),
        "feature": getattr(args, "feature", ""),
        "tags": merged_tags,
        "source_uri": getattr(args, "source_uri", ""),
        "effective_from": getattr(args, "effective_from", None),
        "effective_to": getattr(args, "effective_to", None),
        "type": args.type,
        "content_markdown": content,
        "summary": enriched.summary or infer_summary(content),
        "author": args.author,
        "change_note": change_note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import one document (.md/.txt/.pdf/.docx/image) into knowledge base."
    )
    parser.add_argument("--file", required=True, help="Path to document")
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
    parser.add_argument("--author", default="document-import")
    parser.add_argument("--change-note", default="import document")
    parser.add_argument("--knowledge-item-id", default=None, help="If set, append new version to existing item")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--enable-ocr", action="store_true", default=False, help="Enable OCR fallback/extraction")
    parser.add_argument("--ocr-lang", default="eng", help="OCR language for tesseract")
    parser.add_argument("--ocr-max-pages", type=int, default=20, help="Max pages for PDF OCR")
    args = parser.parse_args()

    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    content = extract_text(
        file_path,
        enable_ocr=args.enable_ocr,
        ocr_lang=args.ocr_lang,
        ocr_max_pages=args.ocr_max_pages,
    )
    if not content.strip():
        raise ValueError("Document has no extractable text")

    args.tags = _parse_tags(args.tags)
    payload = build_payload(args, file_path, content)
    upsert = upsert_payload(payload, args.api_url)
    print(
        json.dumps(
            {
                "status": "ok",
                "file": str(file_path),
                "title": payload["title"],
                "summary": payload["summary"],
                "endpoint": upsert["endpoint"],
                "result": upsert["result"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
