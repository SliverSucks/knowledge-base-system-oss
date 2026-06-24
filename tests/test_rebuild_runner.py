"""``RebuildRunner`` 单元测试（design v1.2 §4.5 + AC10 + AC23）。

通过注入 stub ``rebuild_fn`` / ``backup_fn`` / ``restore_fn`` 绕开真实 embedding
服务依赖（HashEmbedding fallback 会被 strict 拒绝），专测编排逻辑：

- 并发互斥（同时只能跑一个，第二次 → RebuildAlreadyRunning）
- 阈值放行（< 阈值 不置 maintenance；≥ 阈值 置 + finally 必清）
- progress_cb 异常路径（rebuild_fn raise → FAILED）
- abort 触发 restore_fn + 状态 → ABORTED + maintenance flag 清掉
- 完成路径状态 → COMPLETED
"""
from __future__ import annotations

import threading
import time

import pytest

from app.services.maintenance import (
    MaintenanceFlag,
    MaintenanceReason,
)
from app.services.rebuild_runner import (
    RebuildAlreadyRunning,
    RebuildRunner,
    RebuildStatus,
)


class FakeRepo:
    def __init__(self, total: int) -> None:
        self._total = total

    def count_active_chunks(self) -> int:
        return self._total


class FakeVectorIndex:
    pass


def _make_runner():
    """每次给独立 MaintenanceFlag 避免跨用例污染（singleton 默认共享）。"""
    return RebuildRunner(maintenance_flag=MaintenanceFlag())


# ---------------------------------------------------------------------------
# 完成路径
# ---------------------------------------------------------------------------

def test_start_runs_to_completion():
    runner = _make_runner()

    def rebuild(repo, vi, *, batch_size, progress_cb):
        progress_cb(50, 100)
        progress_cb(100, 100)

    snap = runner.start(
        repo=FakeRepo(100), vector_index=FakeVectorIndex(),
        qdrant_local_path="/tmp/qdrant-nx", backup_root="/tmp/backups-nx",
        rebuild_fn=rebuild,
        backup_fn=lambda src, dst: None,    # 跳过真备份
        restore_fn=lambda b, q: None,
        threshold_chunks=1000,
    )
    assert snap.status == RebuildStatus.RUNNING.value
    assert snap.task_id
    assert snap.total == 100

    runner.join(timeout=2.0)
    final = runner.state()
    assert final.status == RebuildStatus.COMPLETED.value
    assert final.processed == 100
    assert final.ended_at > 0
    assert final.threshold_blocked_writes is False


def test_concurrent_start_rejected():
    runner = _make_runner()
    started = threading.Event()
    release = threading.Event()

    def slow_rebuild(repo, vi, *, batch_size, progress_cb):
        started.set()
        release.wait(2.0)
        progress_cb(1, 1)

    runner.start(
        repo=FakeRepo(10), vector_index=FakeVectorIndex(),
        qdrant_local_path="/tmp/qdrant-nx", backup_root="/tmp/backups-nx",
        rebuild_fn=slow_rebuild,
        backup_fn=lambda s, d: None, restore_fn=lambda b, q: None,
    )
    assert started.wait(2.0)

    with pytest.raises(RebuildAlreadyRunning):
        runner.start(
            repo=FakeRepo(10), vector_index=FakeVectorIndex(),
            qdrant_local_path="/tmp/qdrant-nx", backup_root="/tmp/backups-nx",
            rebuild_fn=slow_rebuild,
            backup_fn=lambda s, d: None, restore_fn=lambda b, q: None,
        )

    release.set()
    runner.join(timeout=2.0)


# ---------------------------------------------------------------------------
# 阈值放行（AC10）
# ---------------------------------------------------------------------------

def test_small_index_does_not_set_maintenance():
    flag = MaintenanceFlag()
    runner = RebuildRunner(maintenance_flag=flag)

    def rebuild(repo, vi, *, batch_size, progress_cb):
        progress_cb(1, 1)

    runner.start(
        repo=FakeRepo(100), vector_index=FakeVectorIndex(),
        qdrant_local_path="/tmp/x", backup_root="/tmp/y",
        threshold_chunks=5000,
        rebuild_fn=rebuild,
        backup_fn=lambda s, d: None, restore_fn=lambda b, q: None,
    )
    # 起线程后 flag 不应被置
    assert flag.is_active() is False
    runner.join(timeout=2.0)
    assert flag.is_active() is False
    assert runner.state().threshold_blocked_writes is False


def test_large_index_sets_and_clears_maintenance():
    flag = MaintenanceFlag()
    runner = RebuildRunner(maintenance_flag=flag)

    def rebuild(repo, vi, *, batch_size, progress_cb):
        # 验证执行期间 flag 在
        assert flag.is_active() is True
        assert flag.reason() == MaintenanceReason.REINDEX
        progress_cb(1, 1)

    snap = runner.start(
        repo=FakeRepo(10000), vector_index=FakeVectorIndex(),
        qdrant_local_path="/tmp/x", backup_root="/tmp/y",
        threshold_chunks=5000,
        rebuild_fn=rebuild,
        backup_fn=lambda s, d: None, restore_fn=lambda b, q: None,
    )
    assert snap.threshold_blocked_writes is True
    runner.join(timeout=2.0)
    # finally 必须清掉，否则永久 maintenance 死锁
    assert flag.is_active() is False


def test_large_index_clears_maintenance_on_failure():
    """rebuild_fn 抛异常时也必须清 maintenance flag（finally）。"""
    flag = MaintenanceFlag()
    runner = RebuildRunner(maintenance_flag=flag)

    def rebuild(repo, vi, *, batch_size, progress_cb):
        raise RuntimeError("simulated embed failure")

    runner.start(
        repo=FakeRepo(10000), vector_index=FakeVectorIndex(),
        qdrant_local_path="/tmp/x", backup_root="/tmp/y",
        threshold_chunks=5000,
        rebuild_fn=rebuild,
        backup_fn=lambda s, d: None, restore_fn=lambda b, q: None,
    )
    runner.join(timeout=2.0)
    assert flag.is_active() is False
    final = runner.state()
    assert final.status == RebuildStatus.FAILED.value
    assert "simulated embed failure" in final.error


# ---------------------------------------------------------------------------
# Abort + 回滚（AC23）
# ---------------------------------------------------------------------------

def test_abort_triggers_restore_and_marks_aborted(tmp_path):
    runner = _make_runner()
    restore_calls = []

    def rebuild(repo, vi, *, batch_size, progress_cb):
        # 模拟正在跑：循环调用 progress_cb 直到被 abort 抛出
        for i in range(1000):
            progress_cb(i + 1, 1000)
            time.sleep(0.005)

    def restore(backup, qdrant):
        restore_calls.append((backup, qdrant))

    runner.start(
        repo=FakeRepo(100), vector_index=FakeVectorIndex(),
        qdrant_local_path="/tmp/qdrant-nx", backup_root=str(tmp_path),
        rebuild_fn=rebuild,
        backup_fn=lambda s, d: str(tmp_path / "fake-backup"),
        restore_fn=restore,
        threshold_chunks=1000,
    )
    # 给 worker 起来 + 跑两轮再 abort
    time.sleep(0.05)
    snap = runner.abort(wait_timeout_sec=2.0)
    assert snap.status == RebuildStatus.ABORTED.value
    assert restore_calls, "abort 应触发 restore_fn"
    assert restore_calls[0][0] == str(tmp_path / "fake-backup")


def test_abort_when_idle_returns_idle_state():
    runner = _make_runner()
    snap = runner.abort()
    assert snap.status == RebuildStatus.IDLE.value


# ---------------------------------------------------------------------------
# 进度回传
# ---------------------------------------------------------------------------

def test_progress_cb_updates_processed_field():
    runner = _make_runner()
    snapshots = []

    def rebuild(repo, vi, *, batch_size, progress_cb):
        for i in (10, 50, 100):
            progress_cb(i, 100)
            snapshots.append(runner.state().processed)

    runner.start(
        repo=FakeRepo(100), vector_index=FakeVectorIndex(),
        qdrant_local_path="/tmp/x", backup_root="/tmp/y",
        rebuild_fn=rebuild,
        backup_fn=lambda s, d: None, restore_fn=lambda b, q: None,
        threshold_chunks=1000,
    )
    runner.join(timeout=2.0)
    assert snapshots == [10, 50, 100]
    assert runner.state().processed == 100
