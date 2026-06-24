from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.schemas import SearchRequest, UpsertRequest
from app.service import KnowledgeService


class KnowledgeMcpTools:
    def __init__(self, repo: Any) -> None:
        self.service = KnowledgeService(repo)
        self.project_root = Path(__file__).resolve().parents[1]
        self.require_dangerous_confirm = self._read_bool_env("KB_MCP_REQUIRE_DANGEROUS_CONFIRM", default=False)

    def search_knowledge(
        self,
        query: str,
        domain: str,
        project: str | None = None,
        module: str | None = None,
        feature: str | None = None,
        tags: list[str] | None = None,
        source_uri: str | None = None,
        as_of: datetime | None = None,
        top_k: int = 8,
        actor: str = "codex-local",
    ) -> dict[str, Any]:
        req = SearchRequest(
            query=query,
            domain=domain,
            project=project,
            module=module,
            feature=feature,
            tags=tags or [],
            source_uri=source_uri,
            as_of=as_of,
            top_k=top_k,
            actor=actor,
        )
        return self.service.search(req)

    def get_knowledge_item(self, item_id: str, actor: str = "codex-local") -> dict[str, Any]:
        row = self.service.get_item(item_id, actor=actor)
        if row is None:
            raise ValueError("knowledge item not found")
        return row

    def upsert_knowledge(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = UpsertRequest(**payload)
        return self.service.upsert(req)

    def import_incremental_knowledge(
        self,
        directory: str,
        project: str,
        domain: str = "work",
        knowledge_type: str = "fact",
    ) -> dict[str, Any]:
        return self._run_script("kb-import-incremental.sh", [directory, project, domain, knowledge_type])

    def export_knowledge_package(self, export_dir: str | None = None) -> dict[str, Any]:
        args = [export_dir] if export_dir else []
        return self._run_script("kb-export-package.sh", args)

    def import_knowledge_package(self, package_path: str, confirm: bool = False) -> dict[str, Any]:
        if self.require_dangerous_confirm and not confirm:
            raise ValueError(
                "dangerous operation: set confirm=true to import and restore knowledge package "
                "(KB_MCP_REQUIRE_DANGEROUS_CONFIRM=1)"
            )
        return self._run_script("kb-import-package.sh", [package_path])

    def clear_knowledge_base(self, confirm: bool = False, backup_dir: str | None = None) -> dict[str, Any]:
        if self.require_dangerous_confirm and not confirm:
            raise ValueError(
                "dangerous operation: set confirm=true to clear knowledge base "
                "(KB_MCP_REQUIRE_DANGEROUS_CONFIRM=1)"
            )
        args = ["--yes"]
        if backup_dir:
            args.append(backup_dir)
        return self._run_script("kb-clear.sh", args)

    def cleanup_expired_knowledge(
        self,
        mode: str = "archive",
        as_of: str | None = None,
        backup_dir: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if mode not in ("archive", "delete"):
            raise ValueError("mode must be archive or delete")
        if mode == "delete" and self.require_dangerous_confirm and not confirm:
            raise ValueError(
                "dangerous operation: mode=delete requires confirm=true "
                "(KB_MCP_REQUIRE_DANGEROUS_CONFIRM=1)"
            )

        args = ["--mode", mode]
        if as_of:
            args += ["--as-of", as_of]
        if backup_dir:
            args += ["--backup-dir", backup_dir]
        return self._run_script("kb-clean-expired.sh", args)

    def _run_script(self, script_name: str, args: list[str]) -> dict[str, Any]:
        script_path = self.project_root / "scripts" / script_name
        if not script_path.exists():
            raise FileNotFoundError(f"script not found: {script_path}")

        proc = subprocess.run(
            [str(script_path), *args],
            cwd=str(self.project_root),
            text=True,
            capture_output=True,
            check=False,
        )
        output = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()

        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "script": script_name,
            "args": args,
            "output": output,
            "error": err,
        }

    @staticmethod
    def _read_bool_env(name: str, default: bool = False) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}
