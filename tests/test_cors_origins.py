"""CORS 来源白名单测试。

目标：CORS 中间件只放行本机环回（127.0.0.1 / localhost），
拒绝任意外部来源，从浏览器侧阻断 CSRF 攻击向量。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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
    return TestClient(app)


def _preflight(client: TestClient, origin: str):
    """模拟浏览器 CORS 预检请求。"""
    return client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )


def test_cors_allows_loopback_ipv4(client):
    """127.0.0.1 任意端口预检放行。"""
    r = _preflight(client, "http://127.0.0.1:5173")
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


def test_cors_allows_localhost_any_port(client):
    """localhost 任意端口预检放行（含默认端口）。"""
    r = _preflight(client, "http://localhost:18000")
    assert r.headers.get("access-control-allow-origin") == "http://localhost:18000"

    r2 = _preflight(client, "http://localhost")
    assert r2.headers.get("access-control-allow-origin") == "http://localhost"


def test_cors_allows_https_loopback(client):
    """https 协议环回也放行（覆盖本地自签证书场景）。"""
    r = _preflight(client, "https://127.0.0.1:8443")
    assert r.headers.get("access-control-allow-origin") == "https://127.0.0.1:8443"


def test_cors_allows_ipv6_loopback(client):
    """IPv6 环回 [::1] 也放行（审计 #1 补漏）。"""
    r = _preflight(client, "http://[::1]:18000")
    assert r.headers.get("access-control-allow-origin") == "http://[::1]:18000"


def test_cors_blocks_external_origin(client):
    """外部来源预检不应回 access-control-allow-origin（浏览器据此拦截）。"""
    r = _preflight(client, "https://evil.com")
    assert r.headers.get("access-control-allow-origin") != "https://evil.com"

    r2 = _preflight(client, "http://malicious.example")
    assert r2.headers.get("access-control-allow-origin") != "http://malicious.example"


def test_cors_blocks_lookalike_loopback(client):
    """伪装环回的来源（如 127.0.0.1.evil.com）不应被放行。"""
    r = _preflight(client, "http://127.0.0.1.evil.com")
    assert r.headers.get("access-control-allow-origin") != "http://127.0.0.1.evil.com"


def test_cors_no_origin_header_passes(client):
    """无 Origin 头的请求（如服务端到服务端 / curl）正常返回业务响应。"""
    r = client.get("/health")
    assert r.status_code == 200
