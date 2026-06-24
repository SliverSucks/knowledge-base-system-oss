"""验证 system_config 表新增 embedding_service_* 5 字段的读写与默认值。

覆盖：默认值（老用户无感升级 mode=disabled）/ 往返写读 / 非法值回落 /
managed 布尔转换。对应 openspec embedded-embedding-service v1.2 配置变更。
"""
from __future__ import annotations

import pytest

from app.repository_sqlite import SqliteKnowledgeRepo


@pytest.fixture()
def repo(tmp_path) -> SqliteKnowledgeRepo:
    return SqliteKnowledgeRepo(str(tmp_path / "kb.db"))


def _base_payload() -> dict:
    # upsert 要求 api_base_url / grafana_url 非空。
    return {
        "api_base_url": "http://127.0.0.1:18000",
        "grafana_url": "http://127.0.0.1:3000",
    }


class TestDefaults:
    def test_fresh_db_defaults(self, repo: SqliteKnowledgeRepo) -> None:
        cfg = repo.get_system_config()
        assert cfg["embedding_service_mode"] == "disabled"
        assert cfg["embedding_service_managed"] is False
        assert cfg["embedding_service_model_id"] == ""
        assert cfg["embedding_service_port"] == 0
        assert cfg["embedding_service_device"] == "cpu"


class TestRoundTrip:
    def test_write_read_local_mode(self, repo: SqliteKnowledgeRepo) -> None:
        payload = _base_payload()
        payload.update({
            "embedding_service_mode": "local",
            "embedding_service_managed": True,
            "embedding_service_model_id": "bge-m3",
            "embedding_service_port": 7687,
            "embedding_service_device": "cpu",
        })
        repo.upsert_system_config(payload)
        cfg = repo.get_system_config()
        assert cfg["embedding_service_mode"] == "local"
        assert cfg["embedding_service_managed"] is True
        assert cfg["embedding_service_model_id"] == "bge-m3"
        assert cfg["embedding_service_port"] == 7687
        assert cfg["embedding_service_device"] == "cpu"

    def test_managed_bool_conversion(self, repo: SqliteKnowledgeRepo) -> None:
        payload = _base_payload()
        payload["embedding_service_managed"] = False
        repo.upsert_system_config(payload)
        assert repo.get_system_config()["embedding_service_managed"] is False


class TestInvalidFallback:
    def test_invalid_mode_falls_back_disabled(self, repo: SqliteKnowledgeRepo) -> None:
        payload = _base_payload()
        payload["embedding_service_mode"] = "bogus"
        repo.upsert_system_config(payload)
        assert repo.get_system_config()["embedding_service_mode"] == "disabled"

    def test_invalid_device_falls_back_cpu(self, repo: SqliteKnowledgeRepo) -> None:
        payload = _base_payload()
        payload["embedding_service_device"] = "tpu"
        repo.upsert_system_config(payload)
        assert repo.get_system_config()["embedding_service_device"] == "cpu"

    def test_existing_fields_unaffected(self, repo: SqliteKnowledgeRepo) -> None:
        # 加 5 字段不能破坏既有 embedding_* / rerank_* 字段往返。
        payload = _base_payload()
        payload.update({
            "embedding_enabled": True,
            "embedding_model": "text-embedding-3-small",
            "embedding_dim": 1536,
        })
        repo.upsert_system_config(payload)
        cfg = repo.get_system_config()
        assert cfg["embedding_enabled"] is True
        assert cfg["embedding_model"] == "text-embedding-3-small"
        assert cfg["embedding_dim"] == 1536
