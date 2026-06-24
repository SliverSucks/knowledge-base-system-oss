"""备份包 manifest 解析与 schema_version 校验测试。"""
from __future__ import annotations

import json

import pytest


def _valid_payload(**override):
    base = {
        "schema_version": 1,
        "created_at": "2026-05-19T00:00:00Z",
        "backend": "sqlite",
        "host": "alice-mac",
        "knowledge_db_sha256": "a" * 64,
        "embedding": {"model": "M", "dim": 384, "base_url": "https://x"},
        "stats": {"items": 3, "versions": 3, "chunks": 96, "vectors": 96},
    }
    base.update(override)
    return base


def test_current_schema_version_is_one():
    from app.services.manifest import CURRENT_SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS
    assert CURRENT_SCHEMA_VERSION == 1
    assert 1 in SUPPORTED_SCHEMA_VERSIONS


def test_parse_valid_manifest():
    from app.services.manifest import parse_manifest
    raw = json.dumps(_valid_payload())
    m = parse_manifest(raw)
    assert m.schema_version == 1
    assert m.backend == "sqlite"
    assert m.host == "alice-mac"
    assert m.knowledge_db_sha256 == "a" * 64
    assert m.embedding.model == "M"
    assert m.embedding.dim == 384
    assert m.embedding.base_url == "https://x"
    assert m.stats.items == 3
    assert m.stats.chunks == 96


def test_parse_accepts_bytes_input():
    from app.services.manifest import parse_manifest
    raw = json.dumps(_valid_payload()).encode("utf-8")
    m = parse_manifest(raw)
    assert m.schema_version == 1


def test_parse_rejects_invalid_json():
    from app.services.manifest import ManifestParseError, parse_manifest
    with pytest.raises(ManifestParseError, match="not valid JSON"):
        parse_manifest("{not json")


def test_parse_rejects_missing_schema_version():
    from app.services.manifest import ManifestParseError, parse_manifest
    raw = json.dumps({"backend": "sqlite"})
    with pytest.raises(ManifestParseError, match="schema_version"):
        parse_manifest(raw)


def test_parse_rejects_unknown_schema_version():
    from app.services.manifest import ManifestParseError, parse_manifest
    raw = json.dumps(_valid_payload(schema_version=2))
    with pytest.raises(ManifestParseError, match="schema_version=2 not supported"):
        parse_manifest(raw)


def test_parse_rejects_non_int_schema_version():
    from app.services.manifest import ManifestParseError, parse_manifest
    raw = json.dumps(_valid_payload(schema_version="1"))
    with pytest.raises(ManifestParseError, match="must be int"):
        parse_manifest(raw)


def test_parse_rejects_wrong_sha256_length():
    from app.services.manifest import ManifestParseError, parse_manifest
    raw = json.dumps(_valid_payload(knowledge_db_sha256="short"))
    with pytest.raises(ManifestParseError, match="sha256"):
        parse_manifest(raw)


def test_parse_rejects_missing_required_field():
    from app.services.manifest import ManifestParseError, parse_manifest
    payload = _valid_payload()
    del payload["host"]
    raw = json.dumps(payload)
    with pytest.raises(ManifestParseError, match="host"):
        parse_manifest(raw)


def test_parse_rejects_missing_embedding_dim():
    from app.services.manifest import ManifestParseError, parse_manifest
    payload = _valid_payload(embedding={"model": "M", "base_url": ""})
    raw = json.dumps(payload)
    with pytest.raises(ManifestParseError, match="embedding.dim"):
        parse_manifest(raw)


def test_parse_rejects_missing_stats_chunks():
    from app.services.manifest import ManifestParseError, parse_manifest
    payload = _valid_payload(stats={"items": 0, "versions": 0, "vectors": 0})
    raw = json.dumps(payload)
    with pytest.raises(ManifestParseError, match="stats.chunks"):
        parse_manifest(raw)
