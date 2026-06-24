#!/usr/bin/env python3
"""Codex 集成安装器。

把 MCP server 注册到 Codex 用户级 config.toml。MCP server 走
agent-integration/kb-mcp-proxy.py — 通过 HTTP 调用本地 kb-api，跟部署模式
（docker / 直装版）无关，只要 kb-api 在本地端口可达即可。
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


MCP_SERVER_NAME = "knowledge-base-system"


def _backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.backup.{ts}")
    shutil.copy2(path, backup)
    return backup


def _remove_section(text: str, section_header: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False

    for line in lines:
        stripped = line.strip()
        is_header = stripped.startswith("[") and stripped.endswith("]")

        if is_header and stripped == section_header:
            skipping = True
            continue

        if skipping and is_header:
            skipping = False

        if not skipping:
            out.append(line)

    return "".join(out)


def _ensure_trailing_newline(s: str) -> str:
    return s if s.endswith("\n") else s + "\n"


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_mcp_section(proxy_path: Path, python_bin: str) -> str:
    args = [str(proxy_path)]
    rendered_args = ", ".join(_toml_str(arg) for arg in args)
    return (
        f"[mcp_servers.{MCP_SERVER_NAME}]\n"
        f"command = {_toml_str(python_bin)}\n"
        f"args = [{rendered_args}]\n"
    )


def _build_project_trust_section(project_root: Path) -> str:
    return (
        f"[projects.\"{project_root}\"]\n"
        "trust_level = \"trusted\"\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install knowledge-base-system MCP for Codex (HTTP proxy mode)"
    )
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    parser.add_argument(
        "--python-bin",
        default="python3",
        help="Python 解释器路径，需含 mcp + httpx 包；默认 python3",
    )
    parser.add_argument(
        "--set-project-trust",
        action="store_true",
        help="also mark project as trusted in Codex config",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    codex_home = Path(args.codex_home).resolve()
    codex_home.mkdir(parents=True, exist_ok=True)

    proxy_path = project_root / "agent-integration" / "kb-mcp-proxy.py"
    if not proxy_path.exists():
        raise FileNotFoundError(f"mcp proxy not found: {proxy_path}")

    config_path = codex_home / "config.toml"
    original = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    updated = original
    updated = _remove_section(updated, f"[mcp_servers.{MCP_SERVER_NAME}]")
    updated = _remove_section(updated, f"[mcp_servers.{MCP_SERVER_NAME}.env]")

    if args.set_project_trust:
        updated = _remove_section(updated, f"[projects.\"{project_root}\"]")

    updated = _ensure_trailing_newline(updated).rstrip("\n") + "\n\n"
    updated += _build_mcp_section(proxy_path, python_bin=args.python_bin)

    if args.set_project_trust:
        updated += "\n" + _build_project_trust_section(project_root)

    _backup_file(config_path)
    config_path.write_text(updated, encoding="utf-8")

    print("[OK] Codex integration installed (HTTP proxy mode)")
    print(f"  config: {config_path}")
    print(f"  mcp server: {MCP_SERVER_NAME}")
    print(f"  python: {args.python_bin}")
    print(f"  proxy:  {proxy_path}")
    if args.set_project_trust:
        print(f"  project trust: enabled for {project_root}")

    print("\nNext step:")
    print("  1) ensure kb-api is running locally (default http://127.0.0.1:18000)")
    print("  2) restart Codex")
    print("  3) run: codex mcp list  (should show knowledge-base-system)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
