"""tar 解压 zip slip / 符号链接逃逸测试（审计 #2 / #15）。

通过 monkeypatch 让 tar.extractall 抛 TypeError，强制走
_safe_extractall_fallback 路径，验证恶意成员被拒绝。
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest


def _force_fallback(monkeypatch):
    """让 TarFile.extractall(filter=...) 抛 TypeError，进入 fallback。"""
    original = tarfile.TarFile.extractall

    def shim(self, *a, **kw):
        if "filter" in kw:
            raise TypeError("simulated old Python without filter argument")
        return original(self, *a, **kw)

    monkeypatch.setattr(tarfile.TarFile, "extractall", shim)


def _make_pkg_with_evil_member(tmp_path: Path, evil_name: str) -> Path:
    """构造含越权路径的 tar.gz（带合法 manifest 占位，避免 import 过早失败）。"""
    pkg = tmp_path / "evil.tar.gz"
    with tarfile.open(pkg, "w:gz") as tar:
        manifest = b'{"schema_version":1}'
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
        evil = b"PWN"
        info = tarfile.TarInfo(evil_name)
        info.size = len(evil)
        tar.addfile(info, io.BytesIO(evil))
    return pkg


def _make_pkg_with_symlink(tmp_path: Path) -> Path:
    pkg = tmp_path / "symlink.tar.gz"
    with tarfile.open(pkg, "w:gz") as tar:
        info = tarfile.TarInfo("evil-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        info.size = 0
        tar.addfile(info)
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


def test_fallback_rejects_absolute_path(tmp_path, monkeypatch):
    """老 Python fallback 路径下，绝对路径成员被拒。"""
    from app.services.backup_service import BackupImportError

    _force_fallback(monkeypatch)
    pkg = _make_pkg_with_evil_member(tmp_path, "/etc/passwd-attack")

    svc, auto_svc = _setup(tmp_path)
    with pytest.raises(BackupImportError) as exc:
        svc.import_overwrite(str(pkg), auto_svc)
    assert exc.value.kind == "client"
    assert "unsafe path" in str(exc.value).lower() or "extract" in str(exc.value).lower()


def test_fallback_rejects_dotdot_path(tmp_path, monkeypatch):
    """老 Python fallback 路径下，含 .. 的成员被拒。"""
    from app.services.backup_service import BackupImportError

    _force_fallback(monkeypatch)
    pkg = _make_pkg_with_evil_member(tmp_path, "../../../etc/evil")

    svc, auto_svc = _setup(tmp_path)
    with pytest.raises(BackupImportError) as exc:
        svc.import_overwrite(str(pkg), auto_svc)
    assert exc.value.kind == "client"


def test_fallback_rejects_symlink(tmp_path, monkeypatch):
    """老 Python fallback 路径下，符号链接成员被拒。"""
    from app.services.backup_service import BackupImportError

    _force_fallback(monkeypatch)
    pkg = _make_pkg_with_symlink(tmp_path)

    svc, auto_svc = _setup(tmp_path)
    with pytest.raises(BackupImportError) as exc:
        svc.import_overwrite(str(pkg), auto_svc)
    assert exc.value.kind == "client"
    assert "link" in str(exc.value).lower()


def test_fallback_extracts_safely_when_clean(tmp_path, monkeypatch):
    """老 Python fallback 路径下，合法成员能正常解压。"""
    from app.services.backup_service import (
        BackupImportError,
        _safe_extractall_fallback,
    )

    pkg = _make_pkg_with_evil_member(tmp_path, "data/knowledge.db")
    out = tmp_path / "extracted"
    out.mkdir()
    with tarfile.open(pkg, "r:gz") as tar:
        _safe_extractall_fallback(tar, out)
    assert (out / "manifest.json").exists()
    assert (out / "data" / "knowledge.db").exists()
