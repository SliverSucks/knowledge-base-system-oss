from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


class BaseKnowledgeRepo:
    """共享静态方法基类，隔离数据库无关逻辑。"""

    @staticmethod
    def _chunk_text(content: str, max_chars: int = 800, overlap_chars: int = 100) -> list[str]:
        if overlap_chars >= max_chars:
            overlap_chars = max_chars - 1

        sections = BaseKnowledgeRepo._split_markdown_sections(content)
        if not sections:
            return [content]

        chunks: list[str] = []
        for heading_path, body in sections:
            prefix = "\n".join(heading_path).strip()
            section_text = f"{prefix}\n\n{body}".strip() if prefix else body.strip()
            if not section_text:
                continue

            if len(section_text) <= max_chars:
                chunks.append(section_text)
                continue

            paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
            if not paragraphs:
                paragraphs = [body]
            buffer = prefix
            for para in paragraphs:
                candidate = f"{buffer}\n\n{para}" if buffer else para
                if len(candidate) <= max_chars:
                    buffer = candidate
                    continue
                if buffer and buffer != prefix:
                    chunks.append(buffer)
                if len(para) <= max_chars:
                    buffer = f"{prefix}\n\n{para}" if prefix else para
                else:
                    start = 0
                    while start < len(para):
                        end = start + max_chars
                        piece = para[start:end]
                        piece_text = f"{prefix}\n\n{piece}" if prefix else piece
                        chunks.append(piece_text)
                        start = end - overlap_chars
                        if start >= len(para) - overlap_chars:
                            break
                    buffer = prefix
            if buffer and buffer != prefix:
                chunks.append(buffer)
        return chunks or [content]

    @staticmethod
    def _split_markdown_sections(content: str) -> list[tuple[list[str], str]]:
        """按 markdown 标题切分；返回 (标题链, 正文) 列表。

        标题链记录当前 section 所属的祖先标题（如 ["# 总标题", "## 子标题"]），
        作为正文前缀注入每个 chunk，保留语境，避免向量召回时标题与正文割裂。
        """
        if not content:
            return []
        lines = content.splitlines()
        heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
        fence_re = re.compile(r"^\s*(```|~~~)")
        sections: list[tuple[list[str], str]] = []
        stack: list[tuple[int, str]] = []
        buffer: list[str] = []
        in_code_block = False

        def flush() -> None:
            body = "\n".join(buffer).strip("\n")
            if not body and not stack:
                return
            heading_path = [text for _, text in stack]
            sections.append((heading_path, body))

        for line in lines:
            if fence_re.match(line):
                in_code_block = not in_code_block
                buffer.append(line)
                continue
            if in_code_block:
                buffer.append(line)
                continue
            m = heading_re.match(line)
            if m:
                flush()
                buffer = []
                level = len(m.group(1))
                heading_text = f"{m.group(1)} {m.group(2).strip()}"
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading_text))
            else:
                buffer.append(line)
        flush()
        return sections

    @staticmethod
    def _token_count(text: str) -> int:
        count = 0
        in_cjk = False
        for ch in text:
            code = ord(ch)
            is_cjk = (
                0x4E00 <= code <= 0x9FFF
                or 0x3400 <= code <= 0x4DBF
                or 0x3000 <= code <= 0x303F
                or 0xFF00 <= code <= 0xFFEF
                or 0x2E80 <= code <= 0x2EFF
                or 0xF900 <= code <= 0xFAFF
            )
            if is_cjk:
                count += 1
                in_cjk = False
            elif ch.isspace():
                in_cjk = False
            else:
                if not in_cjk:
                    count += 1
                    in_cjk = True
        return max(count, 1)

    @staticmethod
    def _normalize_tags(raw: Any) -> list[str]:
        if not raw:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for tag in raw:
            val = str(tag).strip().lower()
            if not val or val in seen:
                continue
            seen.add(val)
            out.append(val)
        return out

    @staticmethod
    def _coerce_datetime(raw: Any) -> datetime | None:
        if raw is None:
            return None
        if isinstance(raw, datetime):
            # naive datetime 统一视为 UTC，避免 Postgres TIMESTAMPTZ 受会话时区影响
            if raw.tzinfo is None:
                raw = raw.replace(tzinfo=timezone.utc)
            return raw
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                return None
        return None

    @staticmethod
    def _build_snippet(content: str, query_terms: list[str], width: int = 180) -> str:
        text = re.sub(r"\s+", " ", (content or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
        if not text:
            return ""
        if len(text) <= width:
            return text
        terms = [t.strip() for t in query_terms if t and t.strip()]
        if not terms:
            return text[:width]
        lower = text.lower()
        positions = [lower.find(t.lower()) for t in terms]
        positions = [p for p in positions if p >= 0]
        if not positions:
            return text[:width]
        pos = min(positions)
        start = max(0, pos - width // 2)
        end = min(len(text), start + width)
        start = max(0, end - width)
        return text[start:end]

    @staticmethod
    def _lexical_match_ratio(query_terms: list[str], title: str, snippet: str) -> float:
        if not query_terms:
            return 0.0
        hay = f"{title}\n{snippet}".lower()
        matched = sum(1 for t in query_terms if t.strip().lower() and t.strip().lower() in hay)
        return matched / max(len(query_terms), 1)

    @staticmethod
    def _merge_results(
        *,
        keyword_rows: list[dict[str, Any]],
        vector_rows: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for row in keyword_rows + vector_rows:
            key = row["knowledge_item_id"]
            if key not in merged or row["score"] > merged[key]["score"]:
                merged[key] = row
        ordered = sorted(merged.values(), key=lambda r: r["score"], reverse=True)
        return ordered[:top_k]
