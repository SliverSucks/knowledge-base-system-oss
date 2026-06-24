from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.model_settings import RerankConfig, rerank_config_from_env
from app.text_tokens import token_set

logger = logging.getLogger(__name__)


@dataclass
class LocalLexicalReranker:
    def rerank(self, query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        q = token_set(query)
        if not q:
            return rows

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            title = str(row.get("title", ""))
            snippet = str(row.get("snippet", ""))
            base = float(row.get("score", 0.0))
            d = token_set(title + " " + snippet)
            overlap = len(q & d) / max(len(q), 1)
            # Re-rank score keeps retrieval base score while boosting semantic lexical alignment.
            fused = 0.7 * base + 0.3 * overlap
            enriched = dict(row)
            enriched["score"] = round(fused, 6)
            scored.append((fused, enriched))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored]


@dataclass
class ApiReranker:
    config: RerankConfig

    def rerank(self, query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.config.active or not rows:
            return rows

        docs = [f"{str(r.get('title', '')).strip()}\n{str(r.get('snippet', '')).strip()}".strip() for r in rows]
        payload = {
            "model": self.config.model,
            "query": query,
            "documents": docs,
            "top_n": len(docs),
            "return_documents": False,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}

        try:
            with httpx.Client(timeout=self.config.timeout_sec) as client:
                resp = client.post(f"{self.config.base_url}{self.config.path}", headers=headers, json=payload)
                resp.raise_for_status()
                body = resp.json()
            return _apply_api_rerank_scores(rows, body)
        except Exception:
            logger.warning("API 重排失败，保留原始排序 query=%s", query, exc_info=True)
            return rows


def _apply_api_rerank_scores(rows: list[dict[str, Any]], body: dict[str, Any]) -> list[dict[str, Any]]:
    data = body.get("results")
    if not isinstance(data, list) or not data:
        return rows

    ordered: list[tuple[float, dict[str, Any]]] = []
    used: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        idx_raw = item.get("index")
        try:
            idx = int(idx_raw)
        except Exception:
            logger.debug("重排结果 index 解析失败 idx_raw=%s", idx_raw, exc_info=True)
            continue
        if idx < 0 or idx >= len(rows) or idx in used:
            continue
        used.add(idx)
        base = float(rows[idx].get("score", 0.0))
        rel = float(item.get("relevance_score", base))
        merged = dict(rows[idx])
        merged["score"] = round(0.3 * base + 0.7 * rel, 6)
        ordered.append((merged["score"], merged))

    if not ordered:
        return rows

    for idx, row in enumerate(rows):
        if idx in used:
            continue
        ordered.append((float(row.get("score", 0.0)), dict(row)))

    ordered.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in ordered]


# DB rerank 字段 → env var 映射。空字段会显式 pop env，
# 防止旧值残留（与 vector_index._EMBEDDING_OPTIONAL_ENV_FIELDS 同模式）。
_RERANK_OPTIONAL_ENV_FIELDS: dict[str, str] = {
    "rerank_api_key": "KB_RERANK_API_KEY",
    "rerank_base_url": "KB_RERANK_BASE_URL",
    "rerank_path": "KB_RERANK_PATH",
    "rerank_timeout_sec": "KB_RERANK_TIMEOUT_SEC",
}


def _apply_db_rerank_to_env(db_cfg: dict) -> None:
    """把 DB rerank 配置注入 os.environ，空字段显式 pop 旧值。

    前提：调用方已确认 db_cfg["rerank_enabled"] 且 db_cfg["rerank_model"] 非空。
    """
    os.environ["KB_RERANK_ENABLED"] = "1"
    os.environ["KB_RERANK_MODEL"] = str(db_cfg["rerank_model"])

    for db_key, env_key in _RERANK_OPTIONAL_ENV_FIELDS.items():
        val = db_cfg.get(db_key)
        if val:
            os.environ[env_key] = str(val)
        else:
            os.environ.pop(env_key, None)


def make_reranker(db_cfg: dict | None = None) -> ApiReranker | LocalLexicalReranker:
    """构建 Reranker，db_cfg 中的配置优先于环境变量。"""
    if db_cfg and db_cfg.get("rerank_enabled") and db_cfg.get("rerank_model"):
        _apply_db_rerank_to_env(db_cfg)
    cfg = rerank_config_from_env()
    if cfg.active:
        return ApiReranker(config=cfg)
    return LocalLexicalReranker()


def rerank_results(
    query: str,
    rows: list[dict[str, Any]],
    reranker: ApiReranker | LocalLexicalReranker | None = None,
) -> list[dict[str, Any]]:
    strategy = reranker or make_reranker()
    return strategy.rerank(query=query, rows=rows)
