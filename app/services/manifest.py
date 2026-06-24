"""备份包 manifest 模型与解析。

manifest 是 tar.gz 内 `meta/manifest.json` 的 schema：
- schema_version：当前为 1，未来不兼容变更才 bump
- backend：sqlite | postgres
- knowledge_db_sha256：64 hex 字符（用于 import 解压后比对 db 完整性）
- embedding：写入备份时的 embedding 配置（model + dim + base_url）
- stats：items / versions / chunks / vectors 计数

import 时按顺序校验：schema_version 兼容性 → 字段完整性 → 类型 → sha256 长度。
实际 sha256 内容比对在解压后进行；本模块只负责 manifest 自身的结构校验。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


CURRENT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = (1,)


class ManifestParseError(ValueError):
    """manifest 解析或校验失败。"""


@dataclass
class ManifestEmbedding:
    model: str
    dim: int
    base_url: str


@dataclass
class ManifestStats:
    items: int
    versions: int
    chunks: int
    vectors: int


@dataclass
class Manifest:
    schema_version: int
    created_at: str
    backend: str
    host: str
    knowledge_db_sha256: str
    embedding: ManifestEmbedding
    stats: ManifestStats


def parse_manifest(raw: str | bytes) -> Manifest:
    """解析并校验 manifest JSON。失败抛 ManifestParseError。"""
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ManifestParseError(f"manifest is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ManifestParseError("manifest must be a JSON object")

    if "schema_version" not in data:
        raise ManifestParseError("manifest missing required field: schema_version")

    sv = data["schema_version"]
    if not isinstance(sv, int) or isinstance(sv, bool):
        raise ManifestParseError(
            f"schema_version must be int, got {type(sv).__name__}"
        )

    if sv not in SUPPORTED_SCHEMA_VERSIONS:
        raise ManifestParseError(
            f"backup schema_version={sv} not supported by current service, "
            f"supported={list(SUPPORTED_SCHEMA_VERSIONS)}; "
            f"请升级服务版本到能识别此 schema 的版本"
        )

    required = (
        "created_at",
        "backend",
        "host",
        "knowledge_db_sha256",
        "embedding",
        "stats",
    )
    for k in required:
        if k not in data:
            raise ManifestParseError(f"manifest missing required field: {k}")

    sha = data["knowledge_db_sha256"]
    if not isinstance(sha, str) or len(sha) != 64:
        raise ManifestParseError("knowledge_db_sha256 must be 64-char hex string")

    emb = data["embedding"]
    if not isinstance(emb, dict):
        raise ManifestParseError("embedding must be object")
    for k in ("model", "dim", "base_url"):
        if k not in emb:
            raise ManifestParseError(f"embedding.{k} missing")

    stats = data["stats"]
    if not isinstance(stats, dict):
        raise ManifestParseError("stats must be object")
    for k in ("items", "versions", "chunks", "vectors"):
        if k not in stats:
            raise ManifestParseError(f"stats.{k} missing")

    return Manifest(
        schema_version=sv,
        created_at=str(data["created_at"]),
        backend=str(data["backend"]),
        host=str(data["host"]),
        knowledge_db_sha256=sha,
        embedding=ManifestEmbedding(
            model=str(emb["model"]),
            dim=int(emb["dim"]),
            base_url=str(emb["base_url"]),
        ),
        stats=ManifestStats(
            items=int(stats["items"]),
            versions=int(stats["versions"]),
            chunks=int(stats["chunks"]),
            vectors=int(stats["vectors"]),
        ),
    )
