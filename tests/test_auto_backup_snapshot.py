"""AutoBackupService.snapshot_current_data 测试。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture
def src_dirs(tmp_path):
    db = tmp_path / "data" / "knowledge.db"
    qdrant = tmp_path / "data" / "qdrant_local"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"sqlite file content")
    qdrant.mkdir()
    (qdrant / "v.bin").write_bytes(b"vector")
    (qdrant / "sub").mkdir()
    (qdrant / "sub" / "more.bin").write_bytes(b"sub")
    return db, qdrant


def test_snapshot_creates_timestamped_dir(tmp_path, src_dirs):
    from app.services.backup_service import AutoBackupService

    db, qdrant = src_dirs
    bak_root = tmp_path / "auto-backup"
    svc = AutoBackupService(
        sqlite_path=str(db),
        qdrant_local_path=str(qdrant),
        auto_backup_root=str(bak_root),
    )
    snapshot_path = svc.snapshot_current_data(trigger="import_before")

    snap = Path(snapshot_path)
    assert snap.exists()
    assert (snap / "data" / "knowledge.db").read_bytes() == b"sqlite file content"
    assert (snap / "data" / "qdrant_local" / "v.bin").read_bytes() == b"vector"
    assert (snap / "data" / "qdrant_local" / "sub" / "more.bin").read_bytes() == b"sub"
    manifest = json.loads((snap / "meta" / "manifest.json").read_text())
    assert manifest["trigger"] == "import_before"
    assert "created_at" in manifest
    assert "host" in manifest


def test_snapshot_multiple_calls_distinct_dirs(tmp_path, src_dirs):
    """连续 snapshot 应生成不同的时间戳目录，不互相覆盖。"""
    from app.services.backup_service import AutoBackupService

    db, qdrant = src_dirs
    bak_root = tmp_path / "auto-backup"
    svc = AutoBackupService(
        sqlite_path=str(db),
        qdrant_local_path=str(qdrant),
        auto_backup_root=str(bak_root),
    )
    p1 = svc.snapshot_current_data(trigger="t1")
    time.sleep(1.1)  # 时间戳精度为秒
    p2 = svc.snapshot_current_data(trigger="t2")
    assert p1 != p2
    assert Path(p1).exists()
    assert Path(p2).exists()


def test_snapshot_extra_meta_merged(tmp_path, src_dirs):
    from app.services.backup_service import AutoBackupService

    db, qdrant = src_dirs
    bak_root = tmp_path / "auto-backup"
    svc = AutoBackupService(
        sqlite_path=str(db),
        qdrant_local_path=str(qdrant),
        auto_backup_root=str(bak_root),
    )
    snap_path = svc.snapshot_current_data(
        trigger="install",
        extra_meta={"app_version_before": "1.1.9", "app_version_after": "1.2.0"},
    )
    manifest = json.loads((Path(snap_path) / "meta" / "manifest.json").read_text())
    assert manifest["app_version_before"] == "1.1.9"
    assert manifest["app_version_after"] == "1.2.0"


def test_snapshot_works_without_qdrant_dir(tmp_path):
    """Qdrant 目录不存在时仍正常 snapshot db。"""
    from app.services.backup_service import AutoBackupService

    db = tmp_path / "data" / "knowledge.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"only db")
    bak_root = tmp_path / "auto-backup"

    svc = AutoBackupService(
        sqlite_path=str(db),
        qdrant_local_path=str(tmp_path / "nonexistent"),
        auto_backup_root=str(bak_root),
    )
    snap_path = svc.snapshot_current_data(trigger="t")
    assert (Path(snap_path) / "data" / "knowledge.db").exists()
    assert not (Path(snap_path) / "data" / "qdrant_local").exists()
