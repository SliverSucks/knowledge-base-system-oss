from __future__ import annotations

import os
from datetime import datetime
from typing import Literal

from app.mcp_tools import KnowledgeMcpTools
from app.vector_index import VectorIndex


def _build_tools() -> KnowledgeMcpTools:
    backend = os.getenv("KB_BACKEND", "").strip().lower()
    if not backend:
        raise RuntimeError("KB_BACKEND is not configured; set KB_BACKEND=sqlite or KB_BACKEND=postgres explicitly")

    if backend == "sqlite":
        from app.repository_sqlite import SqliteKnowledgeRepo
        sqlite_path = os.getenv("SQLITE_PATH", "./data/knowledge.db")
        repo = SqliteKnowledgeRepo(sqlite_path=sqlite_path, vector_index=None)
    elif backend == "postgres":
        from app.repository_postgres import PostgresKnowledgeRepo
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL is not configured")
        repo = PostgresKnowledgeRepo(database_url=db_url, vector_index=None)
    else:
        raise RuntimeError(f"不支持的 KB_BACKEND: {backend}，可选值: sqlite, postgres")

    # 一段式 init：从 repo.system_config 拿 DB 配置构建唯一 VectorIndex（与 main.py 同步）。
    repo.vector_index = VectorIndex.from_repo(repo)
    return KnowledgeMcpTools(repo)


def create_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("mcp package is required. Install with: pip install mcp") from exc

    tools = _build_tools()
    mcp = FastMCP("knowledge-base-system")

    @mcp.tool()
    def search_knowledge(
        query: str,
        domain: Literal["work", "personal"],
        project: str | None = None,
        module: str | None = None,
        feature: str | None = None,
        tags: list[str] | None = None,
        source_uri: str | None = None,
        as_of: datetime | None = None,
        top_k: int = 8,
        actor: str = "codex-local",
    ) -> dict:
        return tools.search_knowledge(
            query=query,
            domain=domain,
            project=project,
            module=module,
            feature=feature,
            tags=tags,
            source_uri=source_uri,
            as_of=as_of,
            top_k=top_k,
            actor=actor,
        )

    @mcp.tool()
    def get_knowledge_item(item_id: str, actor: str = "codex-local") -> dict:
        return tools.get_knowledge_item(item_id, actor=actor)

    @mcp.tool()
    def upsert_knowledge(
        title: str,
        domain: Literal["work", "personal"],
        project: str,
        content_markdown: str,
        author: str,
        type: Literal["decision", "runbook", "lesson", "fact"] = "fact",
        summary: str = "",
        change_note: str = "",
        module: str = "",
        feature: str = "",
        tags: list[str] | None = None,
        source_uri: str = "",
        knowledge_item_id: str | None = None,
        public_read: bool = True,
        acl_actors: list[str] | None = None,
    ) -> dict:
        """写入或更新一条知识条目。

        必填（与后端 UpsertRequest 严格对齐，缺失会校验失败）：
        title / domain（work|personal）/ project / content_markdown / author。
        type 仅 decision|runbook|lesson|fact，默认 fact。
        传 knowledge_item_id 为更新并产生新版本，留空为新建。
        """
        return tools.upsert_knowledge({
            "title": title,
            "domain": domain,
            "project": project,
            "content_markdown": content_markdown,
            "author": author,
            "type": type,
            "summary": summary,
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
        return tools.import_incremental_knowledge(
            directory=directory,
            project=project,
            domain=domain,
            knowledge_type=knowledge_type,
        )

    @mcp.tool()
    def export_knowledge_package(export_dir: str | None = None) -> dict:
        return tools.export_knowledge_package(export_dir=export_dir)

    @mcp.tool()
    def import_knowledge_package(package_path: str, confirm: bool = False) -> dict:
        return tools.import_knowledge_package(package_path=package_path, confirm=confirm)

    @mcp.tool()
    def clear_knowledge_base(confirm: bool = False, backup_dir: str | None = None) -> dict:
        return tools.clear_knowledge_base(confirm=confirm, backup_dir=backup_dir)

    @mcp.tool()
    def cleanup_expired_knowledge(
        mode: str = "archive",
        as_of: str | None = None,
        backup_dir: str | None = None,
        confirm: bool = False,
    ) -> dict:
        return tools.cleanup_expired_knowledge(mode=mode, as_of=as_of, backup_dir=backup_dir, confirm=confirm)

    return mcp


if __name__ == "__main__":
    create_server().run()
