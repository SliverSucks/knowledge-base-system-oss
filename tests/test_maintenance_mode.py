"""Maintenance Mode flag 与中间件测试。"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Task 1.2: MaintenanceFlag 单元测试
# ---------------------------------------------------------------------------


def test_default_inactive():
    from app.services.maintenance import MaintenanceFlag
    flag = MaintenanceFlag()
    assert not flag.is_active()
    assert flag.reason() is None


def test_set_and_clear():
    from app.services.maintenance import MaintenanceFlag, MaintenanceReason
    flag = MaintenanceFlag()
    flag.set(MaintenanceReason.BACKUP_IMPORT, detail="overwrite mode")
    assert flag.is_active()
    assert flag.reason() == MaintenanceReason.BACKUP_IMPORT
    assert "overwrite" in flag.detail()

    flag.clear()
    assert not flag.is_active()
    assert flag.reason() is None


def test_set_when_already_active_raises():
    """已置位时再次 set 应抛 RuntimeError（防止并发 import）。"""
    from app.services.maintenance import MaintenanceFlag, MaintenanceReason
    flag = MaintenanceFlag()
    flag.set(MaintenanceReason.BACKUP_IMPORT, detail="first")
    try:
        with pytest.raises(RuntimeError, match="already active"):
            flag.set(MaintenanceReason.PRE_RESTORE_STALE, detail="second")
    finally:
        flag.clear()


def test_reason_enum_values():
    """枚举值与 spec 对齐。"""
    from app.services.maintenance import MaintenanceReason
    assert MaintenanceReason.BACKUP_IMPORT.value == "backup_import"
    assert MaintenanceReason.PRE_RESTORE_STALE.value == "pre_restore_stale"


# ---------------------------------------------------------------------------
# Task 1.3: MaintenanceMiddleware 集成测试
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("VECTOR_ENABLED", "0")

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[server]\nport = 18000\n", encoding="utf-8")
    monkeypatch.setenv("KB_CONFIG_TOML_PATH", str(cfg_path))

    from app.main import _repo_singleton_sqlite, _repo_singleton_postgres
    _repo_singleton_sqlite.cache_clear()
    _repo_singleton_postgres.cache_clear()

    from app.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_middleware_blocks_write_when_active(client):
    from app.services.maintenance import MaintenanceReason, get_maintenance_flag
    flag = get_maintenance_flag()
    flag.clear()
    flag.set(MaintenanceReason.BACKUP_IMPORT, detail="test")
    try:
        resp = client.post(
            "/v1/knowledge/items/upsert",
            json={
                "title": "t",
                "domain": "work",
                "type": "fact",
                "content_markdown": "c",
                "author": "x",
            },
        )
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "60"
        body = resp.json()
        assert "maintenance" in body["detail"].lower()
        assert body.get("reason") == "backup_import"
    finally:
        flag.clear()


def test_middleware_allows_read_when_active(client):
    from app.services.maintenance import MaintenanceReason, get_maintenance_flag
    flag = get_maintenance_flag()
    flag.clear()
    flag.set(MaintenanceReason.BACKUP_IMPORT, detail="test")
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        resp = client.post(
            "/v1/knowledge/search",
            json={"query": "x", "domain": "work"},
        )
        assert resp.status_code == 200
        resp = client.get("/v1/system/config")
        assert resp.status_code == 200
    finally:
        flag.clear()


def test_middleware_no_op_when_inactive(client):
    """flag 未置位时写类路由正常工作。"""
    from app.services.maintenance import get_maintenance_flag
    flag = get_maintenance_flag()
    flag.clear()
    # 不再 set，直接调写类路由（不关心业务是否成功，只要不是 503）
    resp = client.post(
        "/v1/knowledge/items/upsert",
        json={
            "title": "t",
            "domain": "work",
            "type": "fact",
            "content_markdown": "c",
            "author": "x",
        },
    )
    assert resp.status_code != 503
