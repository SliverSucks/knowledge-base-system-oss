#!/usr/bin/env python3
"""Claude Code 集成安装器。

把 SKILL.md 和 MCP server 注册到 Claude Code 用户目录。MCP server 走
agent-integration/kb-mcp-proxy.py — 通过 HTTP 调用本地 kb-api，跟部署模式
（docker / 直装版）无关，只要 kb-api 在本地端口可达即可。
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

SAFE_MCP_TOOLS = [
    "mcp__knowledge-base-system__search_knowledge",
    "mcp__knowledge-base-system__get_knowledge_item",
    "mcp__knowledge-base-system__upsert_knowledge",
    "mcp__knowledge-base-system__import_incremental_knowledge",
    "mcp__knowledge-base-system__export_knowledge_package",
]


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.backup.{ts}")
    shutil.copy2(path, backup)
    return backup


def install_skill(project_root: Path, claude_home: Path) -> Path:
    src = project_root / "agent-integration" / "SKILL.md"
    if not src.exists():
        raise FileNotFoundError(f"skill file not found: {src}")

    dst_dir = claude_home / "skills" / "knowledge-base-first"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "SKILL.md"
    shutil.copy2(src, dst)
    return dst


def install_mcp(project_root: Path, claude_config_path: Path, python_bin: str) -> Path:
    """注册 MCP server 到 Claude Code 用户级主配置文件 `~/.claude.json`。

    这是 Claude Code 实际读取 `mcpServers` 的位置。文件里还包含 oauth、
    projects 等其他字段，必须读出来 merge 后写回，避免覆盖。
    """
    proxy = project_root / "agent-integration" / "kb-mcp-proxy.py"
    if not proxy.exists():
        raise FileNotFoundError(f"mcp proxy not found: {proxy}")

    config = _read_json(claude_config_path, {})
    if "mcpServers" not in config or not isinstance(config["mcpServers"], dict):
        config["mcpServers"] = {}

    config["mcpServers"]["knowledge-base-system"] = {
        "type": "stdio",
        "command": python_bin,
        "args": [str(proxy)],
    }

    _backup_file(claude_config_path)
    claude_config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return claude_config_path


def install_permissions(claude_home: Path) -> tuple[Path, list[str]]:
    settings_path = claude_home / "settings.json"
    settings = _read_json(settings_path, {})

    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions

    allow = permissions.get("allow")
    if not isinstance(allow, list):
        allow = []

    inserted = []
    for item in SAFE_MCP_TOOLS:
        if item not in allow:
            allow.append(item)
            inserted.append(item)

    permissions["allow"] = allow

    _backup_file(settings_path)
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return settings_path, inserted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install knowledge-base-system integration for Claude Code (HTTP proxy mode)"
    )
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--claude-home", default=str(Path.home() / ".claude"),
                        help="Claude Code 用户目录（存放 skills/settings.json），默认 ~/.claude")
    parser.add_argument("--claude-config", default=str(Path.home() / ".claude.json"),
                        help="Claude Code 主配置文件（mcpServers 注册位置），默认 ~/.claude.json")
    parser.add_argument(
        "--python-bin",
        default="python3",
        help="Python 解释器路径，需含 mcp + httpx 包；默认 python3",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    claude_home = Path(args.claude_home).resolve()
    claude_config = Path(args.claude_config).resolve()
    claude_home.mkdir(parents=True, exist_ok=True)

    skill_path = install_skill(project_root, claude_home)
    mcp_path = install_mcp(project_root, claude_config, python_bin=args.python_bin)
    settings_path, inserted = install_permissions(claude_home)

    proxy_path = project_root / "agent-integration" / "kb-mcp-proxy.py"
    print("[OK] Claude integration installed (HTTP proxy mode)")
    print(f"  skill:  {skill_path}")
    print(f"  mcp:    {mcp_path}")
    print(f"  perms:  {settings_path}")
    print(f"  python: {args.python_bin}")
    print(f"  proxy:  {proxy_path}")
    if inserted:
        print("  added permission allow entries:")
        for item in inserted:
            print(f"    - {item}")
    else:
        print("  permission allow entries already existed")

    print("\nNext step:")
    print("  1) ensure kb-api is running locally (default http://127.0.0.1:18000)")
    print("  2) restart Claude Code")
    print("  3) run: claude mcp list  (should show knowledge-base-system)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
