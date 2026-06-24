"""SqliteKnowledgeRepo 单元测试。"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from app.repository_sqlite import SqliteKnowledgeRepo


@pytest.fixture
def repo(tmp_path):
    return SqliteKnowledgeRepo(sqlite_path=str(tmp_path / "test.db"))


def _payload(**kw):
    base = {
        "title": "Test Item",
        "domain": "work",
        "project": "proj-a",
        "type": "decision",
        "content_markdown": "This is the content of the knowledge item.",
        "summary": "Summary",
        "author": "tester",
        "change_note": "initial",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# upsert_item
# ---------------------------------------------------------------------------

class TestUpsertItem:
    def test_insert_returns_id_and_version_1(self, repo):
        r = repo.upsert_item(_payload())
        assert "knowledge_item_id" in r
        assert r["version"] == 1

    def test_update_increments_version(self, repo):
        first = repo.upsert_item(_payload())
        iid = first["knowledge_item_id"]
        second = repo.upsert_item(_payload(knowledge_item_id=iid, title="Updated"))
        assert second["knowledge_item_id"] == iid
        assert second["version"] == 2

    def test_tags_roundtrip(self, repo):
        r = repo.upsert_item(_payload(tags=["python", "backend"]))
        item = repo.get_item(r["knowledge_item_id"])
        assert set(item["tags"]) == {"python", "backend"}

    def test_acl_private_hidden_from_stranger(self, repo):
        r = repo.upsert_item(_payload(public_read=False, acl_actors=["alice"]))
        iid = r["knowledge_item_id"]
        assert repo.get_item(iid, actor="bob") is None
        assert repo.get_item(iid, actor="alice") is not None


# ---------------------------------------------------------------------------
# get_item
# ---------------------------------------------------------------------------

class TestGetItem:
    def test_returns_none_for_unknown_id(self, repo):
        assert repo.get_item("does-not-exist") is None

    def test_returns_full_fields(self, repo):
        r = repo.upsert_item(_payload(title="Hello World"))
        item = repo.get_item(r["knowledge_item_id"])
        assert item is not None
        assert item["title"] == "Hello World"
        assert item["domain"] == "work"
        assert item["content_markdown"] == "This is the content of the knowledge item."
        assert isinstance(item["tags"], list)
        assert "sources" in item


# ---------------------------------------------------------------------------
# delete_item
# ---------------------------------------------------------------------------

class TestDeleteItem:
    def test_soft_delete_hides_item_from_get_and_search(self, repo):
        r = repo.upsert_item(_payload(content_markdown="delete me from console"))
        iid = r["knowledge_item_id"]

        assert repo.delete_item(iid) is True

        assert repo.get_item(iid) is None
        results = repo.search(
            query="delete console",
            domain="work",
            project=None,
            module=None,
            feature=None,
            tags=None,
            source_uri=None,
            as_of=None,
            top_k=5,
        )
        assert results == []

    def test_delete_unknown_item_returns_false(self, repo):
        assert repo.delete_item("does-not-exist") is False

    def test_upsert_rejects_resurrect_of_deleted_item(self, repo):
        r = repo.upsert_item(_payload(content_markdown="will be deleted"))
        iid = r["knowledge_item_id"]
        assert repo.delete_item(iid) is True

        with pytest.raises(ValueError, match="deleted"):
            repo.upsert_item(_payload(knowledge_item_id=iid, content_markdown="resurrect attempt"))

        # 仍然处于已删除状态：详情、搜索都不可见
        assert repo.get_item(iid) is None


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def _search(self, repo, query, domain="work", **kw):
        return repo.search(
            query=query,
            domain=domain,
            project=kw.get("project"),
            module=kw.get("module"),
            feature=kw.get("feature"),
            tags=kw.get("tags"),
            source_uri=kw.get("source_uri"),
            as_of=kw.get("as_of"),
            top_k=kw.get("top_k", 5),
            actor=kw.get("actor"),
        )

    def test_keyword_search_finds_item(self, repo):
        repo.upsert_item(_payload(content_markdown="JWT token strategy for auth"))
        results = self._search(repo, "JWT token", project="proj-a")
        assert len(results) >= 1
        assert results[0]["score"] > 0

    def test_domain_filter_excludes_other_domain(self, repo):
        repo.upsert_item(_payload(domain="work", content_markdown="work specific content"))
        results = self._search(repo, "work specific", domain="personal")
        assert len(results) == 0

    def test_acl_private_hidden_from_non_actor(self, repo):
        repo.upsert_item(_payload(
            content_markdown="secret payload",
            public_read=False,
            acl_actors=["alice"],
        ))
        assert len(self._search(repo, "secret payload", actor="bob")) == 0
        assert len(self._search(repo, "secret payload", actor="alice")) >= 1

    def test_tags_all_must_match(self, repo):
        repo.upsert_item(_payload(
            content_markdown="java monolith service",
            tags=["java"],
        ))
        # item 缺 python，不应命中
        results = self._search(repo, "java monolith", tags=["java", "python"])
        assert len(results) == 0

    def test_source_uri_fuzzy_match(self, repo):
        repo.upsert_item(_payload(
            content_markdown="auth service doc",
            source_uri="https://wiki.internal/auth-service/overview",
        ))
        results = self._search(repo, "auth service", source_uri="auth-service")
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# time window filter
# ---------------------------------------------------------------------------

class TestTimeWindowFilter:
    def _now(self):
        return datetime.now(timezone.utc)

    def _search(self, repo, query, as_of=None):
        return repo.search(
            query=query, domain="work",
            project=None, module=None, feature=None,
            tags=None, source_uri=None, as_of=as_of, top_k=5,
        )

    def test_item_in_window_found(self, repo):
        now = self._now()
        repo.upsert_item(_payload(
            content_markdown="time window content",
            effective_from=(now - timedelta(days=1)).isoformat(),
            effective_to=(now + timedelta(days=1)).isoformat(),
        ))
        assert len(self._search(repo, "time window", as_of=now)) >= 1

    def test_item_before_effective_from_excluded(self, repo):
        now = self._now()
        repo.upsert_item(_payload(
            content_markdown="future knowledge item",
            effective_from=(now + timedelta(days=10)).isoformat(),
        ))
        assert len(self._search(repo, "future knowledge", as_of=now)) == 0

    def test_item_after_effective_to_excluded(self, repo):
        now = self._now()
        repo.upsert_item(_payload(
            content_markdown="expired knowledge item",
            effective_to=(now - timedelta(days=1)).isoformat(),
        ))
        assert len(self._search(repo, "expired knowledge", as_of=now)) == 0

    def test_naive_datetime_does_not_crash(self, repo):
        naive = datetime(2030, 1, 1)
        r = repo.upsert_item(_payload(effective_from=naive))
        assert repo.get_item(r["knowledge_item_id"]) is not None


# ---------------------------------------------------------------------------
# search_chunks_for_ask
# ---------------------------------------------------------------------------

class TestSearchChunksForAsk:
    def test_returns_chunks_with_expected_fields(self, repo):
        repo.upsert_item(_payload(content_markdown="The answer is 42. Ultimate answer."))
        chunks = repo.search_chunks_for_ask(
            query="ultimate answer", domain="work", project="proj-a", top_k=5,
        )
        assert len(chunks) >= 1
        assert "chunk_text" in chunks[0]
        assert "knowledge_item_id" in chunks[0]

    def test_returns_empty_list_for_no_match(self, repo):
        chunks = repo.search_chunks_for_ask(
            query="xyzzy_nonexistent_99999", domain="work", project="proj-a", top_k=5,
        )
        assert chunks == []


# ---------------------------------------------------------------------------
# system_config
# ---------------------------------------------------------------------------

class TestSystemConfig:
    def test_get_returns_defaults_when_no_row(self, repo):
        cfg = repo.get_system_config()
        assert cfg["ui_theme"] == "neo"
        assert cfg["service_port"] == 18000
        assert cfg["llm_enabled"] is False

    def test_upsert_persists_and_returns_new_values(self, repo):
        result = repo.upsert_system_config({
            "ui_theme": "glass",
            "service_port": 18000,
            "api_base_url": "http://127.0.0.1:18000",
            "grafana_url": "http://127.0.0.1:3000",
        })
        assert result["ui_theme"] == "glass"
        assert result["updated_at"] is not None

    def test_upsert_second_call_updates_row(self, repo):
        repo.upsert_system_config({
            "ui_theme": "linear",
            "service_port": 18000,
            "api_base_url": "http://127.0.0.1:18000",
            "grafana_url": "http://127.0.0.1:3000",
        })
        r2 = repo.upsert_system_config({
            "ui_theme": "glass",
            "service_port": 19000,
            "api_base_url": "http://127.0.0.1:19000",
            "grafana_url": "http://127.0.0.1:3000",
        })
        assert r2["ui_theme"] == "glass"
        assert r2["service_port"] == 19000


# ---------------------------------------------------------------------------
# iter_pending_chunks（供 reindex 续传使用）
# ---------------------------------------------------------------------------

class TestIterPendingChunks:
    """iter_pending_chunks 测试。

    业务流：reindex 准备阶段先把目标 chunk 的 vector_id 置 NULL，
    iter_pending_chunks 流式吐 pending chunk，reindex 服务逐 batch 调 embedding 后补 vector_id。
    测试通过手工 UPDATE 模拟 reindex 准备阶段的状态。
    """

    def _long_content(self) -> str:
        """构造跨多个 chunk 的内容（>800 chars）。"""
        paragraph = "this is a paragraph used for chunking test. " * 20
        return (paragraph + "\n\n") * 3

    def _mark_all_pending(self, repo):
        """模拟 reindex 准备阶段：把所有 chunk vector_id 置 NULL。"""
        with repo._connect() as conn:
            conn.execute("UPDATE knowledge_chunk SET vector_id = NULL")

    def test_iter_yields_chunks_with_null_vector_id(self, repo):
        repo.upsert_item(_payload(content_markdown=self._long_content()))
        self._mark_all_pending(repo)

        pending = list(repo.iter_pending_chunks())
        assert len(pending) >= 2, "应至少产生 2 个 chunk 才能验证 iter 行为"
        for chunk in pending:
            assert "id" in chunk and chunk["id"]
            assert "chunk_text" in chunk and chunk["chunk_text"]
            assert chunk.get("vector_id") in (None, "")

    def test_iter_excludes_chunks_with_vector_id(self, repo):
        repo.upsert_item(_payload(content_markdown=self._long_content()))
        self._mark_all_pending(repo)

        first_pass = list(repo.iter_pending_chunks())
        assert len(first_pass) >= 2

        # 给第一个 chunk 写回 vector_id，再 iter 应少一条
        with repo._connect() as conn:
            conn.execute(
                "UPDATE knowledge_chunk SET vector_id = ? WHERE id = ?",
                ("v-fake", first_pass[0]["id"]),
            )

        second_pass = list(repo.iter_pending_chunks())
        assert len(second_pass) == len(first_pass) - 1

    def test_iter_excludes_non_current_version(self, repo):
        """仅 current_version 的 chunk 进入 pending 列表，旧版本 chunk 不出现。"""
        first = repo.upsert_item(_payload(content_markdown=self._long_content()))
        iid = first["knowledge_item_id"]
        # 触发版本递增（生成新 chunk）
        repo.upsert_item(_payload(
            knowledge_item_id=iid,
            content_markdown=self._long_content() + "extra tail.",
        ))
        self._mark_all_pending(repo)

        pending = list(repo.iter_pending_chunks())
        with repo._connect() as conn:
            current_v = conn.execute(
                "SELECT current_version FROM knowledge_item WHERE id = ?", (iid,)
            ).fetchone()["current_version"]
            current_version_id = conn.execute(
                "SELECT id FROM knowledge_version WHERE knowledge_item_id = ? AND version = ?",
                (iid, current_v),
            ).fetchone()["id"]
            for chunk in pending:
                version_row = conn.execute(
                    "SELECT knowledge_version_id FROM knowledge_chunk WHERE id = ?",
                    (chunk["id"],),
                ).fetchone()
                assert version_row["knowledge_version_id"] == current_version_id, (
                    "iter_pending_chunks 不应吐出旧版本的 chunk"
                )

    def test_iter_excludes_deleted_items(self, repo):
        """status != 'active' 的 item 不应进入 pending。"""
        r = repo.upsert_item(_payload(content_markdown=self._long_content()))
        iid = r["knowledge_item_id"]
        self._mark_all_pending(repo)
        with repo._connect() as conn:
            conn.execute("UPDATE knowledge_item SET status='deleted' WHERE id = ?", (iid,))

        pending = list(repo.iter_pending_chunks())
        assert len(pending) == 0


# ---------------------------------------------------------------------------
# clear_all_active_data（供 backup overwrite 使用）
# ---------------------------------------------------------------------------

class TestClearAllActiveData:
    def test_clears_business_tables(self, repo):
        repo.upsert_item(_payload())
        repo.upsert_item(_payload(title="t2"))

        repo.clear_all_active_data()

        with repo._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM knowledge_item").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM knowledge_version").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM knowledge_chunk").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM source_ref").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM acl_policy").fetchone()[0] == 0

    def test_preserves_system_config(self, repo):
        repo.upsert_system_config({
            "ui_theme": "glass",
            "service_port": 18000,
            "api_base_url": "http://127.0.0.1:18000",
            "grafana_url": "http://127.0.0.1:3000",
        })
        repo.upsert_item(_payload())

        repo.clear_all_active_data()

        cfg = repo.get_system_config()
        assert cfg is not None
        assert cfg["ui_theme"] == "glass"
        assert cfg["service_port"] == 18000
