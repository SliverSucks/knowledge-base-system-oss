from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


def _load_proxy_module():
    proxy_path = Path(__file__).resolve().parents[1] / "agent-integration" / "kb-mcp-proxy.py"
    spec = importlib.util.spec_from_file_location("kb_mcp_proxy_under_test", proxy_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kb_mcp_proxy_exposes_8_tools():
    proxy_path = Path(__file__).resolve().parents[1] / "agent-integration" / "kb-mcp-proxy.py"
    text = proxy_path.read_text(encoding="utf-8")

    tool_count = text.count("@mcp.tool()")
    assert tool_count == 8

    expected_defs = [
        "def search_knowledge(",
        "def get_knowledge_item(",
        "def upsert_knowledge(",
        "def import_incremental_knowledge(",
        "def export_knowledge_package(",
        "def import_knowledge_package(",
        "def clear_knowledge_base(",
        "def cleanup_expired_knowledge(",
    ]
    for item in expected_defs:
        assert item in text


def test_upsert_schema_matches_backend_constraints():
    """回归保护：MCP 暴露的 upsert schema 必须与后端 UpsertRequest 真实约束对齐。

    历史 bug：schema 把 project 标成可选、domain/type 标成自由字符串，
    调用方按宽松 schema 填参被后端 422 拒绝。修复后枚举与必填必须透传出来。
    """
    module = _load_proxy_module()
    tools = asyncio.run(module.mcp.list_tools())
    upsert = next(t for t in tools if t.name == "upsert_knowledge")
    schema = upsert.inputSchema
    props = schema["properties"]

    required = set(schema.get("required", []))
    assert {"title", "domain", "project", "content_markdown"}.issubset(required)

    assert props["domain"].get("enum") == ["work", "personal"]
    assert props["type"].get("enum") == ["decision", "runbook", "lesson", "fact"]


def test_format_http_error_surfaces_field_level_detail():
    """422 报错必须带字段级 detail，而非裸的 'Unprocessable Entity'。"""
    import io
    import urllib.error

    module = _load_proxy_module()
    body = (
        b'{"detail":[{"loc":["body","project"],"msg":"Field required","type":"missing"}]}'
    )
    err = urllib.error.HTTPError("http://x/upsert", 422, "Unprocessable Entity", {}, io.BytesIO(body))
    msg = module._format_http_error("/v1/knowledge/items/upsert", err)

    assert "422" in msg
    assert "project" in msg
    assert "Field required" in msg
