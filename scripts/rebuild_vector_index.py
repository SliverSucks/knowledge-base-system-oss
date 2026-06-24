#!/usr/bin/env python3
"""全量重建向量索引（strict mode）。

用途：切换 embedding 模型 / 修复脏索引时，删旧 collection → 重新 embed 全部
active chunk。对应 openspec embedded-embedding-service v1.2 §4.5 + AC20。

strict mode（核心约束）：embedding 调用失败时**立即 fail 整个 rebuild**，绝不
降级 HashEmbedding 写入——否则 hash 向量混进新模型索引，搜索质量崩且无从察觉。
故本脚本直接调 ApiEmbedding.embed（绕开 VectorIndex._embed_with_fallback 的
永久降级逻辑）。

安全：CLI 在 reset/rebuild 前先备份 qdrant_local；strict 失败时旧备份仍在，
按提示可手动恢复。
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("rebuild_vector_index")


class RebuildError(RuntimeError):
    """rebuild 过程的业务异常（strict 失败 / 前置条件不满足）。"""


@dataclass
class RebuildReport:
    total: int = 0
    processed: int = 0
    failed_chunk_ids: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    dry_run: bool = False
    backup_path: Optional[str] = None


def _is_api_embedding(embedding: Any) -> bool:
    """判断 VectorIndex 当前 embedding 是否为真实 API embedding（非 HashEmbedding）。

    strict rebuild 不允许用 hash 重建——否则等于把无语义向量固化进索引。
    """
    # 延迟 import 避免脚本在无 app 环境下 import 失败。
    from app.vector_index import ApiEmbedding
    return isinstance(embedding, ApiEmbedding)


def rebuild_index(
    repo: Any,
    vector_index: Any,
    *,
    batch_size: int = 100,
    dry_run: bool = False,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> RebuildReport:
    """全量 strict 重建（核心逻辑，可单测）。

    流程：校验真实 embedding → 删/重建 collection → reset vector_id → 流式
    strict embed + qdrant upsert + 回写 vector_id。任一 embed 失败立即抛 RebuildError。

    repo 需提供：count_active_chunks / reset_all_vector_ids /
        iter_active_chunks_for_reindex / set_chunk_vector_ids
    vector_index 需提供：embedding(.embed/.dim) / _client / collection_name
    """
    started = time.monotonic()
    report = RebuildReport(dry_run=dry_run)
    report.total = repo.count_active_chunks()

    if dry_run:
        report.elapsed_sec = time.monotonic() - started
        return report

    if not _is_api_embedding(vector_index.embedding):
        raise RebuildError(
            "strict rebuild 拒绝执行：当前未配置真实 embedding 服务（embedding 为 "
            "HashEmbedding 兜底）。请先在 /settings 配好 embedding 或启用本地服务。"
        )
    client = getattr(vector_index, "_client", None)
    if client is None:
        raise RebuildError("Qdrant 客户端不可用，无法重建（检查 VECTOR_ENABLED / qdrant 连接）")

    from qdrant_client import models

    # 删旧建新 collection，按当前 embedding dim（切模型 dim 可能变）。
    dim = int(vector_index.embedding.dim)
    client.recreate_collection(
        collection_name=vector_index.collection_name,
        vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
    )
    repo.reset_all_vector_ids()

    batch_ids: list[str] = []
    batch_points: list[Any] = []

    def _flush() -> None:
        if not batch_points:
            return
        client.upsert(collection_name=vector_index.collection_name, points=batch_points)
        repo.set_chunk_vector_ids(batch_ids)
        batch_ids.clear()
        batch_points.clear()

    for row in repo.iter_active_chunks_for_reindex(batch_size=batch_size):
        chunk_id = row["chunk_id"]
        try:
            vector = vector_index.embedding.embed(row["text"])  # strict：失败即抛
        except Exception as exc:  # noqa: BLE001 —— strict 不吞异常，立即终止
            _flush()  # 已成功的 batch 落盘，便于续传
            raise RebuildError(
                f"embedding 失败于 chunk_id={chunk_id}，strict 模式终止 rebuild "
                f"（已处理 {report.processed} 条）；旧索引备份仍在，可恢复后重试"
            ) from exc
        batch_points.append(
            models.PointStruct(
                id=chunk_id,
                vector=vector,
                payload={
                    "knowledge_item_id": row["knowledge_item_id"],
                    "domain": row["domain"],
                    "project": row["project"],
                    "version": row["version"],
                    "title": row["title"],
                    "chunk_index": row["chunk_index"],
                },
            )
        )
        batch_ids.append(chunk_id)
        report.processed += 1
        if progress_cb is not None:
            progress_cb(report.processed, report.total)
        if len(batch_points) >= batch_size:
            _flush()

    _flush()
    report.elapsed_sec = time.monotonic() - started
    return report


def _backup_qdrant(qdrant_local_path: str, backup_root: str) -> Optional[str]:
    """rebuild 前备份本地 qdrant 目录，返回备份路径（无目录则返回 None）。"""
    src = Path(qdrant_local_path)
    if not src.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dst = Path(backup_root) / f"rebuild-{ts}" / "qdrant_local"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    return str(dst)


def _build_runtime(data_root: str):
    """构造 repo + VectorIndex（从 DB 配置），供 CLI 入口使用。"""
    from app.repository_sqlite import SqliteKnowledgeRepo
    from app.vector_index import VectorIndex

    repo = SqliteKnowledgeRepo(str(Path(data_root) / "knowledge.db"))
    vector_index = VectorIndex.from_repo(repo)
    return repo, vector_index


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="全量重建向量索引（strict mode）")
    parser.add_argument("--data-root", default="./data", help="数据根目录（含 knowledge.db / qdrant_local）")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true", help="只统计待重建数量，不改任何数据")
    parser.add_argument("--no-backup", action="store_true", help="跳过 qdrant_local 备份（危险）")
    parser.add_argument("--backup-root", default=None, help="备份根目录，默认 <data-root>/backups")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data_root = args.data_root
    repo, vector_index = _build_runtime(data_root)

    backup_path: Optional[str] = None
    if not args.dry_run and not args.no_backup:
        qdrant_path = getattr(vector_index, "qdrant_local_path", None) or str(Path(data_root) / "qdrant_local")
        backup_root = args.backup_root or str(Path(data_root) / "backups")
        backup_path = _backup_qdrant(qdrant_path, backup_root)
        if backup_path:
            logger.info("已备份 qdrant_local → %s", backup_path)

    def _progress(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            logger.info("rebuild 进度 %d/%d", done, total)

    try:
        report = rebuild_index(
            repo, vector_index,
            batch_size=args.batch_size, dry_run=args.dry_run, progress_cb=_progress,
        )
    except RebuildError as exc:
        logger.error("rebuild 失败：%s", exc)
        if backup_path:
            logger.error("可从备份恢复：%s", backup_path)
        return 1

    report.backup_path = backup_path
    if args.dry_run:
        logger.info("[dry-run] 待重建 active chunk: %d", report.total)
    else:
        logger.info(
            "rebuild 完成：处理 %d/%d，耗时 %.1fs，备份 %s",
            report.processed, report.total, report.elapsed_sec, backup_path or "(跳过)",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
