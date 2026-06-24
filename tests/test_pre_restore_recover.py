"""`.pre-restore.*` 启动检测与 recover 端点测试（审计 #7 / #8）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def data_dirs(tmp_path):
    sqlite_path = tmp_path / "data" / "knowledge.db"
    qdrant_path = tmp_path / "data" / "qdrant_local"
    sqlite_path.parent.mkdir(parents=True)
    sqlite_path.write_bytes(b"CURRENT-DB")
    qdrant_path.mkdir()
    (qdrant_path / "live.bin").write_bytes(b"LIVE-VEC")
    return sqlite_path, qdrant_path


def _make_pre_restore(sqlite_path: Path, qdrant_path: Path):
    pre_db = sqlite_path.parent / ".pre-restore.bak"
    pre_qdrant = sqlite_path.parent / ".pre-restore-qdrant"
    pre_db.write_bytes(b"OLD-DB-BEFORE-IMPORT")
    pre_qdrant.mkdir()
    (pre_qdrant / "old.bin").write_bytes(b"OLD-VEC")
    return pre_db, pre_qdrant


# ---------------------------------------------------------------------------
# 单元：probe / detect_and_warn / execute_recover
# ---------------------------------------------------------------------------


def test_probe_returns_no_residue_when_clean(data_dirs):
    from app.services.pre_restore_recover import probe
    sqlite_path, qdrant_path = data_dirs
    s = probe(str(sqlite_path), str(qdrant_path))
    assert not s.has_any


def test_probe_detects_both(data_dirs):
    from app.services.pre_restore_recover import probe
    sqlite_path, qdrant_path = data_dirs
    _make_pre_restore(sqlite_path, qdrant_path)

    s = probe(str(sqlite_path), str(qdrant_path))
    assert s.has_db
    assert s.has_qdrant
    assert s.has_any


def test_detect_and_warn_sets_maintenance(data_dirs):
    from app.services.maintenance import MaintenanceReason, get_maintenance_flag
    from app.services.pre_restore_recover import detect_and_warn

    sqlite_path, qdrant_path = data_dirs
    _make_pre_restore(sqlite_path, qdrant_path)

    flag = get_maintenance_flag()
    flag.clear()
    try:
        state = detect_and_warn(str(sqlite_path), str(qdrant_path))
        assert state is not None and state.has_any
        assert flag.is_active()
        assert flag.reason() == MaintenanceReason.PRE_RESTORE_STALE
    finally:
        flag.clear()


def test_detect_and_warn_noop_when_clean(data_dirs):
    from app.services.maintenance import get_maintenance_flag
    from app.services.pre_restore_recover import detect_and_warn

    sqlite_path, qdrant_path = data_dirs
    flag = get_maintenance_flag()
    flag.clear()
    state = detect_and_warn(str(sqlite_path), str(qdrant_path))
    assert state is None
    assert not flag.is_active()


def test_execute_recover_rollback(data_dirs):
    from app.services.maintenance import MaintenanceReason, get_maintenance_flag
    from app.services.pre_restore_recover import detect_and_warn, execute_recover

    sqlite_path, qdrant_path = data_dirs
    pre_db, pre_qdrant = _make_pre_restore(sqlite_path, qdrant_path)
    flag = get_maintenance_flag()
    flag.clear()
    detect_and_warn(str(sqlite_path), str(qdrant_path))
    assert flag.is_active()

    try:
        result = execute_recover(
            action="rollback",
            sqlite_path=str(sqlite_path),
            qdrant_local_path=str(qdrant_path),
        )
        assert result["ok"] is True
        assert result["action"] == "rollback"
        # 数据已被 .pre-restore.* 覆盖
        assert sqlite_path.read_bytes() == b"OLD-DB-BEFORE-IMPORT"
        assert (qdrant_path / "old.bin").read_bytes() == b"OLD-VEC"
        # .pre-restore.* 已消费
        assert not pre_db.exists()
        assert not pre_qdrant.exists()
        # maintenance flag 自动清除
        assert not flag.is_active()
    finally:
        flag.clear()


def test_execute_recover_discard(data_dirs):
    from app.services.maintenance import get_maintenance_flag
    from app.services.pre_restore_recover import detect_and_warn, execute_recover

    sqlite_path, qdrant_path = data_dirs
    pre_db, pre_qdrant = _make_pre_restore(sqlite_path, qdrant_path)
    flag = get_maintenance_flag()
    flag.clear()
    detect_and_warn(str(sqlite_path), str(qdrant_path))

    try:
        result = execute_recover(
            action="discard",
            sqlite_path=str(sqlite_path),
            qdrant_local_path=str(qdrant_path),
        )
        assert result["action"] == "discard"
        # 现行 data 未变
        assert sqlite_path.read_bytes() == b"CURRENT-DB"
        assert (qdrant_path / "live.bin").read_bytes() == b"LIVE-VEC"
        # .pre-restore.* 已删
        assert not pre_db.exists()
        assert not pre_qdrant.exists()
        assert not flag.is_active()
    finally:
        flag.clear()


def test_execute_recover_unknown_action_raises(data_dirs):
    from app.services.pre_restore_recover import execute_recover
    sqlite_path, qdrant_path = data_dirs
    with pytest.raises(ValueError, match="unknown action"):
        execute_recover(
            action="nuke",
            sqlite_path=str(sqlite_path),
            qdrant_local_path=str(qdrant_path),
        )


# ---------------------------------------------------------------------------
# 集成：HTTP 路由 POST /v1/system/recover/pre-restore
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "data" / "knowledge.db"))
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "data" / "qdrant_local"))
    monkeypatch.setenv("VECTOR_ENABLED", "0")

    (tmp_path / "data").mkdir()
    # 用真实的 sqlite repo 把 db 文件落地（合法 schema），否则 get_repo 打开会
    # 报 "file is not a database"
    from app.repository_sqlite import SqliteKnowledgeRepo
    SqliteKnowledgeRepo(
        sqlite_path=str(tmp_path / "data" / "knowledge.db"),
        vector_index=None,
    )
    (tmp_path / "data" / "qdrant_local").mkdir()
    (tmp_path / "data" / "qdrant_local" / "live").write_bytes(b"V")

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[server]\nport = 18000\n", encoding="utf-8")
    monkeypatch.setenv("KB_CONFIG_TOML_PATH", str(cfg_path))

    from app.main import _repo_singleton_sqlite, _repo_singleton_postgres
    _repo_singleton_sqlite.cache_clear()
    _repo_singleton_postgres.cache_clear()
    from app.services.maintenance import get_maintenance_flag
    get_maintenance_flag().clear()

    from app.main import app
    return TestClient(app), tmp_path


def test_route_rejects_weak_confirm(client):
    tc, _ = client
    resp = tc.post(
        "/v1/system/recover/pre-restore",
        data={"action": "rollback", "confirm": "true"},
    )
    assert resp.status_code == 400


def test_route_rejects_token_action_mismatch(client):
    tc, _ = client
    resp = tc.post(
        "/v1/system/recover/pre-restore",
        data={"action": "rollback", "confirm": "I-CONFIRM-DISCARD"},
    )
    assert resp.status_code == 400


def test_route_rejects_unknown_action(client):
    tc, _ = client
    resp = tc.post(
        "/v1/system/recover/pre-restore",
        data={"action": "nuke", "confirm": "I-CONFIRM-DISCARD"},
    )
    assert resp.status_code == 400


def test_route_rollback_full_path(client):
    tc, tmp_path = client
    sqlite_path = tmp_path / "data" / "knowledge.db"
    qdrant_path = tmp_path / "data" / "qdrant_local"
    # .pre-restore.bak 用合法 sqlite 备份（用临时 repo 生成）
    from app.repository_sqlite import SqliteKnowledgeRepo
    tmp_pre_db_src = tmp_path / "scratch-pre.db"
    SqliteKnowledgeRepo(sqlite_path=str(tmp_pre_db_src), vector_index=None)
    import shutil as _sh
    pre_db = sqlite_path.parent / ".pre-restore.bak"
    _sh.copy2(tmp_pre_db_src, pre_db)
    # 不同字节内容用 sqlite_master 序列区分困难；这里只断言 rollback 后字节
    # 与 .pre-restore.bak 一致即可
    expected_bytes = pre_db.read_bytes()

    pre_qdrant = sqlite_path.parent / ".pre-restore-qdrant"
    pre_qdrant.mkdir()
    (pre_qdrant / "old").write_bytes(b"OV")

    from app.services.maintenance import MaintenanceReason, get_maintenance_flag
    flag = get_maintenance_flag()
    flag.clear()
    flag.set(MaintenanceReason.PRE_RESTORE_STALE, detail="test")
    try:
        resp = tc.post(
            "/v1/system/recover/pre-restore",
            data={"action": "rollback", "confirm": "I-CONFIRM-ROLLBACK"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["action"] == "rollback"
        assert sqlite_path.read_bytes() == expected_bytes
        assert not pre_db.exists()
        assert not pre_qdrant.exists()
        assert not flag.is_active()
    finally:
        flag.clear()


def test_route_discard_full_path(client):
    tc, tmp_path = client
    sqlite_path = tmp_path / "data" / "knowledge.db"
    current_bytes = sqlite_path.read_bytes()
    pre_db = sqlite_path.parent / ".pre-restore.bak"
    pre_db.write_bytes(b"OLD")

    from app.services.maintenance import MaintenanceReason, get_maintenance_flag
    flag = get_maintenance_flag()
    flag.clear()
    flag.set(MaintenanceReason.PRE_RESTORE_STALE, detail="test")
    try:
        resp = tc.post(
            "/v1/system/recover/pre-restore",
            data={"action": "discard", "confirm": "I-CONFIRM-DISCARD"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "discard"
        # 现行 data 未变（与 fixture 落地的合法 sqlite 字节相同）
        assert sqlite_path.read_bytes() == current_bytes
        assert not pre_db.exists()
        assert not flag.is_active()
    finally:
        flag.clear()


def test_recover_route_passes_maintenance_middleware(client):
    """recover 端点必须能在 maintenance 期间被调用（middleware 已放行）。"""
    tc, tmp_path = client
    sqlite_path = tmp_path / "data" / "knowledge.db"
    pre_db = sqlite_path.parent / ".pre-restore.bak"
    pre_db.write_bytes(b"O")
    pre_qdrant = sqlite_path.parent / ".pre-restore-qdrant"
    pre_qdrant.mkdir()

    from app.services.maintenance import MaintenanceReason, get_maintenance_flag
    flag = get_maintenance_flag()
    flag.clear()
    flag.set(MaintenanceReason.PRE_RESTORE_STALE, detail="启动检测")
    try:
        resp = tc.post(
            "/v1/system/recover/pre-restore",
            data={"action": "discard", "confirm": "I-CONFIRM-DISCARD"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        flag.clear()
