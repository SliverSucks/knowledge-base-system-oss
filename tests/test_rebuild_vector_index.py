"""验证全量 strict 重建（scripts/rebuild_vector_index）+ repo reindex 数据方法。

覆盖：
- repo: iter_active_chunks_for_reindex 字段完整性 / count / reset / set 回写
- rebuild_index: dry-run 不动数据 / strict 拒绝 hash / 正常重建回写 / embed 失败立即终止
"""
from __future__ import annotations

from typing import Any

import pytest

from app.repository_sqlite import SqliteKnowledgeRepo
from scripts.rebuild_vector_index import RebuildError, rebuild_index


# ---------------- repo 数据方法（真 sqlite） ----------------

@pytest.fixture()
def repo(tmp_path) -> SqliteKnowledgeRepo:
    return SqliteKnowledgeRepo(str(tmp_path / "kb.db"))


def _seed_item(repo: SqliteKnowledgeRepo, title: str = "T") -> dict:
    # chunk 由 _chunk_text(content_markdown) 自动切分，数量不写死，测试用动态 count。
    return repo.upsert_item({
        "title": title,
        "domain": "d1",
        "project": "p1",
        "type": "doc",
        "author": "tester",
        "content_markdown": "hello world paragraph one.\n\nsecond paragraph here.",
    })


class TestRepoReindexMethods:
    def test_iter_active_chunks_fields_complete(self, repo: SqliteKnowledgeRepo) -> None:
        _seed_item(repo)
        rows = list(repo.iter_active_chunks_for_reindex(batch_size=10))
        n = repo.count_active_chunks()
        assert n >= 1
        assert len(rows) == n
        r = rows[0]
        # upsert_chunks payload 需要的字段必须齐全
        for key in ("chunk_id", "text", "chunk_index", "knowledge_item_id",
                    "version", "domain", "project", "title"):
            assert key in r, f"缺字段 {key}"
        assert r["domain"] == "d1"
        assert r["project"] == "p1"

    def test_count_active_chunks(self, repo: SqliteKnowledgeRepo) -> None:
        assert repo.count_active_chunks() == 0
        _seed_item(repo)
        assert repo.count_active_chunks() >= 1

    def test_reset_and_set_vector_ids(self, repo: SqliteKnowledgeRepo) -> None:
        _seed_item(repo)
        n = repo.count_active_chunks()
        rows = list(repo.iter_active_chunks_for_reindex())
        ids = [r["chunk_id"] for r in rows]
        repo.set_chunk_vector_ids(ids)
        # 回写后 pending 应为空（vector_id 已填）
        assert list(repo.iter_pending_chunks()) == []
        # reset 后全部回到 pending
        affected = repo.reset_all_vector_ids()
        assert affected == n
        assert len(list(repo.iter_pending_chunks())) == n


# ---------------- rebuild_index 核心逻辑（stub） ----------------

class _StubEmbedding:
    """模拟 ApiEmbedding：dim 固定，可注入第 N 次 embed 抛错。"""
    dim = 8

    def __init__(self, fail_at: int | None = None) -> None:
        self.calls = 0
        self.fail_at = fail_at

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise RuntimeError("simulated embedding API failure")
        return [0.1] * self.dim


class _StubHashEmbedding:
    dim = 8

    def embed(self, text: str) -> list[float]:
        return [0.0] * self.dim


class _StubQdrantClient:
    def __init__(self) -> None:
        self.recreated = False
        self.upserted_points: list[Any] = []

    def recreate_collection(self, **kwargs: Any) -> None:
        self.recreated = True

    def upsert(self, *, collection_name: str, points: list[Any]) -> None:
        self.upserted_points.extend(points)


class _StubVectorIndex:
    collection_name = "knowledge_chunks"

    def __init__(self, embedding: Any) -> None:
        self.embedding = embedding
        self._client = _StubQdrantClient()


class _StubRepo:
    def __init__(self, n: int) -> None:
        self._rows = [
            {"chunk_id": f"c{i}", "text": f"t{i}", "chunk_index": i,
             "knowledge_item_id": "k1", "version": 1,
             "domain": "d", "project": "p", "title": "T"}
            for i in range(n)
        ]
        self.reset_called = False
        self.written_ids: list[str] = []

    def count_active_chunks(self) -> int:
        return len(self._rows)

    def reset_all_vector_ids(self) -> int:
        self.reset_called = True
        return len(self._rows)

    def iter_active_chunks_for_reindex(self, batch_size: int = 100):
        yield from self._rows

    def set_chunk_vector_ids(self, ids: list[str]) -> None:
        self.written_ids.extend(ids)


def _patch_api_embedding(monkeypatch, embedding_cls) -> None:
    # rebuild_index 用 isinstance(embedding, ApiEmbedding) 判定真实 embedding。
    import scripts.rebuild_vector_index as mod
    monkeypatch.setattr(mod, "_is_api_embedding", lambda emb: isinstance(emb, embedding_cls))


class TestRebuildIndex:
    def test_dry_run_touches_nothing(self) -> None:
        repo = _StubRepo(5)
        vi = _StubVectorIndex(_StubEmbedding())
        report = rebuild_index(repo, vi, dry_run=True)
        assert report.total == 5
        assert report.processed == 0
        assert repo.reset_called is False
        assert vi._client.recreated is False

    def test_strict_rejects_hash_embedding(self, monkeypatch) -> None:
        repo = _StubRepo(3)
        vi = _StubVectorIndex(_StubHashEmbedding())
        _patch_api_embedding(monkeypatch, _StubEmbedding)  # hash 不是 ApiEmbedding
        with pytest.raises(RebuildError, match="未配置真实 embedding"):
            rebuild_index(repo, vi)

    def test_full_rebuild_writes_back(self, monkeypatch) -> None:
        repo = _StubRepo(3)
        emb = _StubEmbedding()
        vi = _StubVectorIndex(emb)
        _patch_api_embedding(monkeypatch, _StubEmbedding)
        report = rebuild_index(repo, vi, batch_size=2)
        assert report.processed == 3
        assert repo.reset_called is True
        assert vi._client.recreated is True
        assert len(vi._client.upserted_points) == 3
        assert repo.written_ids == ["c0", "c1", "c2"]

    def test_embed_failure_stops_immediately(self, monkeypatch) -> None:
        repo = _StubRepo(5)
        emb = _StubEmbedding(fail_at=3)   # 第 3 次 embed 抛错
        vi = _StubVectorIndex(emb)
        _patch_api_embedding(monkeypatch, _StubEmbedding)
        with pytest.raises(RebuildError, match="strict 模式终止"):
            rebuild_index(repo, vi, batch_size=10)
        # strict：失败时不应继续 embed 剩余 chunk
        assert emb.calls == 3
        # 失败前已成功的 2 条 batch 落盘（便于续传）
        assert repo.written_ids == ["c0", "c1"]
