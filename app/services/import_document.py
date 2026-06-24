"""单文件导入解析模块。

把 md / markdown / txt / docx / pdf 文件解析成知识库可入库的 dict，
提供给 POST /v1/knowledge/import-file 端点 in-process 调用。

设计要点：
- 解析逻辑全部 in-process，依赖（pypdf / python-docx）随 PyInstaller binary 一起发，
  用户机器不需要装 Python 包；
- 不带 OCR（Pillow / pytesseract / 系统 tesseract 都不引入），图片格式视为不支持；
- 不携带任何 HTTP 客户端（httpx）；upsert 由调用方走 KnowledgeService.upsert in-process 完成。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".docx", ".pdf")


class ImportDocumentError(Exception):
    """单文件导入相关的基类异常。"""


class UnsupportedFileTypeError(ImportDocumentError):
    """文件后缀不在支持列表里（例如图片、未知后缀）。"""


class EmptyDocumentError(ImportDocumentError):
    """成功解析但没有任何可入库文本。"""


class ParseDependencyError(ImportDocumentError):
    """解析依赖缺失（pypdf / python-docx 没装齐）。生产环境理论上不会触发，
    PyInstaller binary 已经把依赖嵌进去；保留兜底是为了开发态早爆。"""


@dataclass
class ParsedDocument:
    title: str
    content_markdown: str
    summary: str
    tags: list[str]


def _extract_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_docx(path: Path) -> str:
    try:
        from docx import Document  # type: ignore
    except ImportError as exc:
        raise ParseDependencyError(
            "python-docx 未安装，无法解析 .docx 文件"
        ) from exc
    doc = Document(str(path))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise ParseDependencyError(
            "pypdf 未安装，无法解析 .pdf 文件"
        ) from exc
    reader = PdfReader(str(path))
    chunks: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            chunks.append(text)
    return "\n\n".join(chunks).strip()


def extract_text(path: Path) -> str:
    """按后缀分发到对应解析器，返回纯 markdown 文本。

    扫描件 PDF（无文字层）当前会返回空串，调用方应判断后再决定怎么处理。
    """
    suffix = path.suffix.lower()
    if suffix in (".md", ".markdown", ".txt"):
        return _extract_markdown(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    raise UnsupportedFileTypeError(
        f"不支持的文件类型：{suffix}（支持：{', '.join(SUPPORTED_SUFFIXES)}）"
    )


def infer_title(path: Path, content: str) -> str:
    """优先取第一行 markdown 标题（# 开头），否则用文件名。"""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return path.stem


def infer_summary(content: str) -> str:
    """取首段（跳过空行和标题行），截断 120 字符。"""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:120]
    return "Imported from document"


def _normalize_tags(raw_tags: list[str] | None) -> list[str]:
    """tag 去空白、去重、小写化，保留顺序。"""
    if not raw_tags:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tag in raw_tags:
        normalized = tag.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def parse_document(
    path: Path,
    *,
    project: str,
    domain: str,
    knowledge_type: str = "fact",
    actor: str = "manual",
    title: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    module: str = "",
    feature: str = "",
    source_uri: str = "",
    change_note: str = "import via single-file upload",
) -> dict[str, Any]:
    """把文件 + 元数据解析成 KnowledgeService.upsert 可吃的 dict。

    端点 POST /v1/knowledge/import-file 的入口函数。

    Raises:
        UnsupportedFileTypeError: 后缀不在支持列表
        EmptyDocumentError: 解析后正文为空（扫描件 PDF / 空文件）
        ParseDependencyError: 开发态依赖缺失（生产用 PyInstaller binary 不会触发）
        FileNotFoundError: 文件不存在
    """
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")

    content = extract_text(path)
    if not content.strip():
        raise EmptyDocumentError(
            f"文件 {path.name} 解析后没有可入库文本"
            f"（PDF 扫描件无文字层时常见；本期不带 OCR）"
        )

    resolved_title = title or infer_title(path, content)
    resolved_summary = summary or infer_summary(content)
    resolved_tags = _normalize_tags(tags)

    return {
        "title": resolved_title,
        "domain": domain,
        "project": project,
        "module": module,
        "feature": feature,
        "tags": resolved_tags,
        "source_uri": source_uri,
        "type": knowledge_type,
        "content_markdown": content,
        "summary": resolved_summary,
        "author": actor,
        "change_note": change_note,
    }
