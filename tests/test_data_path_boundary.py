"""数据目录路径边界校验测试（审计 #12 二次收紧后）。

默认白名单仅含：仓库根 / /Applications/KnowledgeBase /
~/Library/Application Support/KnowledgeBase。其他路径必须显式通过
KB_DATA_ROOTS 注入；否则任何指向白名单外的 SQLITE_PATH /
QDRANT_LOCAL_PATH / KB_AUTO_BACKUP_ROOT 都应被拒。
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException


def test_validate_data_path_rejects_etc(monkeypatch):
    """显式不在白名单的 /etc 必须被拒。"""
    monkeypatch.delenv("KB_DATA_ROOTS", raising=False)
    from app.main import _validate_data_path
    with pytest.raises(HTTPException) as exc:
        _validate_data_path("/etc/passwd")
    assert exc.value.status_code == 500
    assert "outside allowed roots" in str(exc.value.detail)


def test_validate_data_path_rejects_home_by_default(monkeypatch):
    """$HOME（非 Library/Application Support/KnowledgeBase）也必须被拒。"""
    monkeypatch.delenv("KB_DATA_ROOTS", raising=False)
    from app.main import _validate_data_path
    home = os.path.expanduser("~")
    with pytest.raises(HTTPException) as exc:
        _validate_data_path(os.path.join(home, "photos", "kb.db"))
    assert exc.value.status_code == 500


def test_validate_data_path_allows_kb_data_root_override(monkeypatch, tmp_path):
    """KB_DATA_ROOTS 显式注入后允许指向该目录。"""
    monkeypatch.setenv("KB_DATA_ROOTS", str(tmp_path))
    from app.main import _validate_data_path
    target = tmp_path / "kb.db"
    # 文件不存在也能 resolve（_validate_data_path 不要求存在）
    result = _validate_data_path(str(target))
    assert result == str(target.resolve())


def test_validate_data_path_rejects_traversal_outside_root(monkeypatch, tmp_path):
    """KB_DATA_ROOTS 限制后，./../ 试图越权也被拒。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.setenv("KB_DATA_ROOTS", str(sub))
    from app.main import _validate_data_path
    # 父目录 tmp_path 不在白名单内
    with pytest.raises(HTTPException):
        _validate_data_path(str(tmp_path / "outside.db"))


def test_validate_data_path_allows_kb_app_support(monkeypatch):
    """~/Library/Application Support/KnowledgeBase 在默认白名单内（auto-backup 唯一合法位置）。"""
    monkeypatch.delenv("KB_DATA_ROOTS", raising=False)
    from app.main import _validate_data_path
    target = os.path.expanduser(
        "~/Library/Application Support/KnowledgeBase/auto-backup/test"
    )
    result = _validate_data_path(target)
    assert "KnowledgeBase" in result


def test_validate_data_path_resolves_symlink_then_checks(monkeypatch, tmp_path):
    """符号链接 realpath 解析后再校验：链向白名单外仍被拒。"""
    monkeypatch.setenv("KB_DATA_ROOTS", str(tmp_path))
    from app.main import _validate_data_path
    target_outside = tmp_path.parent / "fake-outside"
    target_outside.mkdir(exist_ok=True)
    sym = tmp_path / "sym"
    if sym.exists():
        sym.unlink()
    sym.symlink_to(target_outside)
    # sym 看起来在 tmp_path 内，但 realpath 后指向外部
    with pytest.raises(HTTPException):
        _validate_data_path(str(sym / "trap.db"))
