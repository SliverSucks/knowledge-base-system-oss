#!/usr/bin/env python3
"""百变怪芝士包 MCP 代理
通过 HTTP 调用运行中的 kb-api，以 stdio MCP 服务暴露给 Claude Code / Codex。
依赖：pip install mcp httpx  （不需要 venv，系统 Python 即可）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Literal

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("错误：请先安装 mcp 包：pip install mcp", file=sys.stderr)
    sys.exit(1)


def _load_port() -> int:
    # 脚本位于 {install_dir}\agent-integration\，config.toml 在 {install_dir}\config\
    cfg = Path(__file__).parent.parent / "config" / "config.toml"
    if cfg.exists():
        try:
            import tomllib  # type: ignore[import]
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[import,no-redef]
            except ImportError:
                tomllib = None  # type: ignore[assignment]
        if tomllib:
            with open(cfg, "rb") as f:
                data = tomllib.load(f)
            return int(data.get("server", {}).get("port", 18000))
    return int(os.environ.get("KB_PORT", 18000))


PORT = _load_port()
BASE = f"http://127.0.0.1:{PORT}"

mcp = FastMCP("knowledge-base-system")


def _format_http_error(path: str, exc: urllib.error.HTTPError) -> str:
    """把后端 HTTPError 的响应体解析出来，尽量给出字段级报错。

    FastAPI 的 422 会在 body.detail 里带 [{loc, msg, type}, ...]，
    裸抛 HTTPError 只会显示 'Unprocessable Entity'，调用方根本看不到哪个字段错。
    这里把 detail 提取成可读文本，省得调用方靠翻源码定位。
    """
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    detail: object = raw
    try:
        parsed = json.loads(raw)
        detail = parsed.get("detail", parsed) if isinstance(parsed, dict) else parsed
    except Exception:
        pass

    # 把 pydantic/FastAPI 的 detail 数组压成「字段: 原因」列表
    if isinstance(detail, list):
        lines = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(p) for p in item.get("loc", []) if p != "body")
                lines.append(f"{loc or '(root)'}: {item.get('msg', item)}")
            else:
                lines.append(str(item))
        detail = "; ".join(lines)

    return f"HTTP {exc.code} from {path}: {detail}"


def _http_post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_format_http_error(path, exc)) from exc


def _http_get(path: str) -> dict:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_format_http_error(path, exc)) from exc


@mcp.tool()
def search_knowledge(
    query: str,
    domain: Literal["work", "personal"],
    project: str | None = None,
    top_k: int = 8,
    actor: str = "mcp-proxy",
) -> dict:
    """在知识库中检索相关条目（混合关键词 + 向量）。

    约束（与后端 SearchRequest 对齐）：
    - domain：必填，仅 `work` | `personal`（传 `person` 会被规范成 `personal`）。
    - project：可选，未明确项目时不要硬填。
    - top_k：1~50，默认 8。
    """
    return _http_post("/v1/knowledge/search", {
        "query": query,
        "domain": domain,
        "project": project,
        "top_k": top_k,
        "actor": actor,
    })


@mcp.tool()
def get_knowledge_item(item_id: str, actor: str = "mcp-proxy") -> dict:
    """按 ID 获取知识条目完整内容"""
    return _http_get(f"/v1/knowledge/items/{item_id}?actor={actor}")


@mcp.tool()
def upsert_knowledge(
    title: str,
    domain: Literal["work", "personal"],
    project: str,
    content_markdown: str,
    type: Literal["decision", "runbook", "lesson", "fact"] = "fact",
    summary: str = "",
    author: str = "mcp-proxy",
    change_note: str = "via mcp",
    module: str = "",
    feature: str = "",
    tags: list[str] | None = None,
    source_uri: str = "",
    knowledge_item_id: str | None = None,
    public_read: bool = True,
    acl_actors: list[str] | None = None,
) -> dict:
    """写入或更新一条知识条目。

    必填字段（与后端 UpsertRequest 严格对齐，缺失会被后端 422 拒绝）：
    - title：知识标题。
    - domain：仅 `work` | `personal`。
    - project：所属项目名，不能为空。
    - content_markdown：正文（Markdown），不能为空。

    枚举字段：
    - type：仅 `decision` | `runbook` | `lesson` | `fact`，默认 `fact`。

    其余可选：module / feature / tags / source_uri / summary / change_note /
    public_read（默认 True）/ acl_actors（public_read=false 时生效）。
    传 knowledge_item_id 为更新并产生新版本，留空为新建。
    """
    return _http_post("/v1/knowledge/items/upsert", {
        "title": title,
        "domain": domain,
        "content_markdown": content_markdown,
        "project": project,
        "type": type,
        "summary": summary,
        "author": author,
        "change_note": change_note,
        "module": module,
        "feature": feature,
        "tags": tags or [],
        "source_uri": source_uri,
        "knowledge_item_id": knowledge_item_id,
        "public_read": public_read,
        "acl_actors": acl_actors or [],
    })


@mcp.tool()
def import_incremental_knowledge(
    directory: str,
    project: str,
    domain: str = "work",
    knowledge_type: str = "fact",
) -> dict:
    """增量导入目录中的知识文件"""
    return _http_post("/v1/knowledge/import-incremental", {
        "directory": directory,
        "project": project,
        "domain": domain,
        "knowledge_type": knowledge_type,
    })


@mcp.tool()
def export_knowledge_package(export_dir: str | None = None) -> dict:
    """导出知识库为可迁移知识包"""
    return _http_post("/v1/knowledge/export-package", {"export_dir": export_dir})


@mcp.tool()
def import_knowledge_package(package_path: str, confirm: bool = False) -> dict:
    """导入知识包恢复知识库（危险操作）"""
    return _http_post("/v1/knowledge/import-package", {
        "package_path": package_path,
        "confirm": confirm,
    })


@mcp.tool()
def clear_knowledge_base(confirm: bool = False, backup_dir: str | None = None) -> dict:
    """清空知识库（危险操作）"""
    return _http_post("/v1/knowledge/clear", {
        "confirm": confirm,
        "backup_dir": backup_dir,
    })


@mcp.tool()
def cleanup_expired_knowledge(
    mode: str = "archive",
    as_of: str | None = None,
    backup_dir: str | None = None,
    confirm: bool = False,
) -> dict:
    """清理过期知识（mode=delete 为危险操作）"""
    return _http_post("/v1/knowledge/cleanup-expired", {
        "mode": mode,
        "as_of": as_of,
        "backup_dir": backup_dir,
        "confirm": confirm,
    })


if __name__ == "__main__":
    mcp.run()
