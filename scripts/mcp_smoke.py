from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _tool_data(result: Any) -> dict[str, Any]:
    if getattr(result, "structuredContent", None):
        return result.structuredContent

    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            maybe = json.loads(text)
            if isinstance(maybe, dict):
                return maybe
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"Cannot parse MCP tool result: {result!r}")


async def main() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python_bin = os.path.join(project_root, ".venv", "Scripts", "python.exe")
    if not os.path.exists(python_bin):
        python_bin = os.path.join(project_root, ".venv", "bin", "python")
    if not os.path.exists(python_bin):
        python_bin = sys.executable

    sqlite_path = os.getenv("SQLITE_PATH", os.path.join(project_root, "data", "knowledge.db"))
    qdrant_path = os.getenv("QDRANT_LOCAL_PATH", os.path.join(project_root, "data", "qdrant_local"))

    server = StdioServerParameters(
        command=python_bin,
        args=["-m", "app.mcp_server"],
        env={
            **os.environ,
            "KB_BACKEND": "sqlite",
            "SQLITE_PATH": sqlite_path,
            "VECTOR_ENABLED": "1",
            "QDRANT_MODE": "local",
            "QDRANT_LOCAL_PATH": qdrant_path,
        },
        cwd=project_root,
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("TOOLS:", ", ".join(sorted(names)))

            upsert_payload: dict[str, Any] = {
                "title": "MCP smoke auth strategy",
                "domain": "work",
                "project": "project-a",
                "type": "decision",
                "content_markdown": "Use short-lived access token and refresh token.",
                "summary": "mcp smoke",
                "author": "smoke",
                "change_note": "initial",
            }
            upsert = await session.call_tool("upsert_knowledge", {"payload": upsert_payload})
            upsert_data = _tool_data(upsert)
            item_id = upsert_data["knowledge_item_id"]
            print("UPSERT:", json.dumps(upsert_data, ensure_ascii=False))

            search = await session.call_tool(
                "search_knowledge",
                {
                    "query": "refresh token",
                    "domain": "work",
                    "project": "project-a",
                    "top_k": 3,
                    "actor": "smoke",
                },
            )
            search_data = _tool_data(search)
            print("SEARCH_COUNT:", len(search_data["results"]))

            got = await session.call_tool("get_knowledge_item", {"item_id": item_id})
            got_data = _tool_data(got)
            print("GET_TITLE:", got_data["title"])


if __name__ == "__main__":
    asyncio.run(main())
