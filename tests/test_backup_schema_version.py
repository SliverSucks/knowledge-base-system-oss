"""备份包 schema_version 校验：未来版本与缺失字段拒绝。"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest


def _make_pkg_with_manifest(tmp_path: Path, manifest: dict) -> Path:
    work = tmp_path / "build"
    work.mkdir()
    (work / "data").mkdir()
    db = work / "data" / "knowledge.db"
    db.write_bytes(b"fake db")
    (work / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pkg = tmp_path / "out.tar.gz"
    with tarfile.open(pkg, "w:gz") as tar:
        for item in sorted(work.rglob("*")):
            if item.is_file():
                tar.add(item, arcname=str(item.relative_to(work)))
    return pkg


def _setup(tmp_path):
    from app.repository_sqlite import SqliteKnowledgeRepo
    from app.services.backup_service import AutoBackupService, BackupService

    db = tmp_path / "data" / "knowledge.db"
    db.parent.mkdir(parents=True)
    repo = SqliteKnowledgeRepo(sqlite_path=str(db), vector_index=None)
    qdrant = tmp_path / "data" / "qdrant_local"
    qdrant.mkdir()
    bak_root = tmp_path / "bak"

    svc = BackupService(
        repo=repo,
        sqlite_path=str(db),
        qdrant_local_path=str(qdrant),
        on_qdrant_close=lambda: None,
        on_qdrant_reinit=lambda: None,
    )
    auto_svc = AutoBackupService(
        sqlite_path=str(db),
        qdrant_local_path=str(qdrant),
        auto_backup_root=str(bak_root),
    )
    return svc, auto_svc


def test_import_rejects_unknown_schema_version(tmp_path):
    from app.services.backup_service import BackupImportError

    pkg = _make_pkg_with_manifest(tmp_path, {
        "schema_version": 2,
        "created_at": "2026-05-19T00:00:00Z",
        "backend": "sqlite",
        "host": "h",
        "knowledge_db_sha256": "a" * 64,
        "embedding": {"model": "M", "dim": 384, "base_url": ""},
        "stats": {"items": 0, "versions": 0, "chunks": 0, "vectors": 0},
    })
    svc, auto_svc = _setup(tmp_path)

    with pytest.raises(BackupImportError) as exc:
        svc.import_overwrite(str(pkg), auto_svc)
    assert "schema_version=2" in str(exc.value)


def test_import_rejects_missing_schema_version(tmp_path):
    from app.services.backup_service import BackupImportError

    pkg = _make_pkg_with_manifest(tmp_path, {
        "backend": "sqlite",
        "host": "h",
        "knowledge_db_sha256": "a" * 64,
        "embedding": {"model": "M", "dim": 384, "base_url": ""},
        "stats": {"items": 0, "versions": 0, "chunks": 0, "vectors": 0},
    })
    svc, auto_svc = _setup(tmp_path)

    with pytest.raises(BackupImportError) as exc:
        svc.import_overwrite(str(pkg), auto_svc)
    assert "schema_version" in str(exc.value)


def test_import_rejects_invalid_manifest_json(tmp_path):
    """manifest 不是合法 JSON 也拒绝。"""
    from app.services.backup_service import BackupImportError

    work = tmp_path / "build"
    work.mkdir()
    (work / "data").mkdir()
    (work / "data" / "knowledge.db").write_bytes(b"x")
    (work / "manifest.json").write_text("{not valid", encoding="utf-8")
    pkg = tmp_path / "out.tar.gz"
    with tarfile.open(pkg, "w:gz") as tar:
        for item in sorted(work.rglob("*")):
            if item.is_file():
                tar.add(item, arcname=str(item.relative_to(work)))

    svc, auto_svc = _setup(tmp_path)
    with pytest.raises(BackupImportError):
        svc.import_overwrite(str(pkg), auto_svc)
