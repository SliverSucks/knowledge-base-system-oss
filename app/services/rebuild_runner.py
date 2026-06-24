"""后台 rebuild 编排（design v1.2 §4.5 + AC10 + AC23）。

负责：
- 单实例并发互斥（同时只允许一个 rebuild 跑）
- 阈值放行：``count_active_chunks >= REINDEX_MAINTENANCE_THRESHOLD`` 才置
  ``MaintenanceReason.REINDEX``（小库后台跑不锁；大库写类 API 返 202）
- 起后台线程跑 ``scripts.rebuild_vector_index.rebuild_index``，progress_cb
  里检查 abort flag → 抛 RebuildAborted 立即终止
- abort 时回滚 qdrant_local 备份（AC23 逃生通道）
- finally 清 maintenance flag，避免异常路径死锁

测试通过注入 ``rebuild_fn`` / ``backup_fn`` / ``restore_fn`` / ``clock`` 解耦真
实 embedding 服务依赖。
"""
from __future__ import annotations

import logging
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from app.services.embedding_install import REINDEX_MAINTENANCE_THRESHOLD
from app.services.maintenance import (
    MaintenanceFlag,
    MaintenanceReason,
    get_maintenance_flag,
)

logger = logging.getLogger(__name__)


class RebuildStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class RebuildAlreadyRunning(Exception):
    """已有 rebuild 在跑，拒绝并发启动（409）。"""


class RebuildAborted(Exception):
    """progress_cb 检测到 abort flag 时抛，rebuild_index 不捕获，runner 接住。"""


@dataclass
class RebuildState:
    status: str = RebuildStatus.IDLE.value
    task_id: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    total: int = 0
    processed: int = 0
    error: str = ""
    backup_path: str = ""
    # 本次 rebuild 是否因 chunk ≥ 阈值而置 maintenance flag（AC10）
    threshold_blocked_writes: bool = False


def _default_backup_fn(qdrant_local_path: str, backup_root: str) -> Optional[str]:
    """默认备份实现：复制整个 qdrant_local 目录到 backups/rebuild-{ts}/。"""
    src = Path(qdrant_local_path)
    if not src.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    dst = Path(backup_root) / f"rebuild-{ts}" / "qdrant_local"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    return str(dst)


def _default_restore_fn(backup_path: str, qdrant_local_path: str) -> None:
    """默认回滚实现：删除当前 qdrant_local + 把 backup 拷回来（AC23）。"""
    src = Path(backup_path)
    dst = Path(qdrant_local_path)
    if not src.exists():
        logger.warning("rebuild abort: backup %s 不存在，无法回滚", backup_path)
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


class RebuildRunner:
    """单实例后台 rebuild 编排。"""

    def __init__(self, maintenance_flag: Optional[MaintenanceFlag] = None) -> None:
        # RLock：start() 持锁期间会调用 self.state() 取快照返回，state() 也要
        # 拿同一把锁——非可重入锁会自死锁。
        self._lock = threading.RLock()
        self._state = RebuildState()
        self._abort_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._maintenance = maintenance_flag or get_maintenance_flag()

    # ------------------------------------------------------------------ 状态

    def state(self) -> RebuildState:
        with self._lock:
            return RebuildState(
                status=self._state.status,
                task_id=self._state.task_id,
                started_at=self._state.started_at,
                ended_at=self._state.ended_at,
                total=self._state.total,
                processed=self._state.processed,
                error=self._state.error,
                backup_path=self._state.backup_path,
                threshold_blocked_writes=self._state.threshold_blocked_writes,
            )

    def is_running(self) -> bool:
        return self._state.status == RebuildStatus.RUNNING.value

    # ------------------------------------------------------------------ 控制

    def start(
        self,
        *,
        repo: Any,
        vector_index: Any,
        qdrant_local_path: str,
        backup_root: str,
        batch_size: int = 100,
        threshold_chunks: int = REINDEX_MAINTENANCE_THRESHOLD,
        rebuild_fn: Optional[Callable[..., Any]] = None,
        backup_fn: Callable[[str, str], Optional[str]] = _default_backup_fn,
        restore_fn: Callable[[str, str], None] = _default_restore_fn,
    ) -> RebuildState:
        """启动后台 rebuild；并发已运行 → RebuildAlreadyRunning。

        ``rebuild_fn`` 可注入（测试用 stub 绕开真实 embedding 服务）；默认
        ``scripts.rebuild_vector_index.rebuild_index``。
        """
        with self._lock:
            if self.is_running():
                raise RebuildAlreadyRunning(
                    f"rebuild 正在运行 (task_id={self._state.task_id})"
                )
            total = repo.count_active_chunks()
            should_block = total >= threshold_chunks
            # 大库走 maintenance flag 挡写（写类 API 返 202，见 main.py 中间件）
            if should_block:
                try:
                    self._maintenance.set(
                        MaintenanceReason.REINDEX,
                        f"rebuild_vector_index running ({total} chunks)",
                    )
                except RuntimeError as exc:
                    # 已有其他原因的 maintenance 在跑（如 backup_import），拒启
                    raise RebuildAlreadyRunning(
                        f"另一项维护任务正在运行：{exc}"
                    ) from exc

            self._abort_event.clear()
            self._state = RebuildState(
                status=RebuildStatus.RUNNING.value,
                task_id=secrets.token_urlsafe(12),
                started_at=time.time(),
                total=total,
                threshold_blocked_writes=should_block,
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(repo, vector_index, qdrant_local_path, backup_root,
                      batch_size, rebuild_fn, backup_fn, restore_fn,
                      should_block),
                daemon=True,
                name=f"rebuild-{self._state.task_id}",
            )
            self._thread.start()
            return self.state()

    def abort(self, *, wait_timeout_sec: float = 10.0) -> RebuildState:
        """触发 abort + 阻塞等线程退出（默认 ≤10s）+ 返回最终状态。"""
        if not self.is_running():
            return self.state()
        self._abort_event.set()
        t = self._thread
        if t is not None:
            t.join(timeout=wait_timeout_sec)
        return self.state()

    def join(self, timeout: Optional[float] = None) -> None:
        """测试用：等线程跑完。"""
        t = self._thread
        if t is not None:
            t.join(timeout)

    def reset_for_tests(self) -> None:
        """测试钩子。"""
        # 若有线程残留先 abort 等回收
        if self.is_running():
            self.abort(wait_timeout_sec=2.0)
        with self._lock:
            self._state = RebuildState()
            self._abort_event.clear()
            self._thread = None
        # 清 maintenance flag（若未清干净）
        try:
            self._maintenance.clear()
        except Exception:
            pass

    # ------------------------------------------------------------------ 内部

    def _make_progress_cb(self) -> Callable[[int, int], None]:
        def _cb(done: int, total: int) -> None:
            if self._abort_event.is_set():
                raise RebuildAborted("rebuild aborted by user")
            with self._lock:
                self._state.processed = done
                if total and self._state.total != total:
                    self._state.total = total
        return _cb

    def _run(
        self,
        repo: Any,
        vector_index: Any,
        qdrant_local_path: str,
        backup_root: str,
        batch_size: int,
        rebuild_fn: Optional[Callable[..., Any]],
        backup_fn: Callable[[str, str], Optional[str]],
        restore_fn: Callable[[str, str], None],
        should_block: bool,
    ) -> None:
        if rebuild_fn is None:
            from scripts.rebuild_vector_index import rebuild_index as _default
            rebuild_fn = _default

        backup_path: Optional[str] = None
        try:
            backup_path = backup_fn(qdrant_local_path, backup_root)
            if backup_path:
                with self._lock:
                    self._state.backup_path = backup_path

            rebuild_fn(
                repo, vector_index,
                batch_size=batch_size,
                progress_cb=self._make_progress_cb(),
            )

            with self._lock:
                self._state.status = RebuildStatus.COMPLETED.value
                self._state.ended_at = time.time()
        except RebuildAborted as exc:
            logger.info("rebuild aborted: %s", exc)
            if backup_path:
                try:
                    restore_fn(backup_path, qdrant_local_path)
                except Exception:  # noqa: BLE001 —— 回滚失败也得让 abort 流程闭合
                    logger.exception("rebuild rollback failed")
            with self._lock:
                self._state.status = RebuildStatus.ABORTED.value
                self._state.ended_at = time.time()
        except Exception as exc:  # noqa: BLE001
            logger.exception("rebuild failed")
            with self._lock:
                self._state.status = RebuildStatus.FAILED.value
                self._state.error = str(exc)
                self._state.ended_at = time.time()
        finally:
            if should_block:
                try:
                    self._maintenance.clear()
                except Exception:
                    logger.exception("rebuild finally: maintenance.clear() failed")


_singleton: Optional[RebuildRunner] = None
_singleton_lock = threading.Lock()


def get_rebuild_runner() -> RebuildRunner:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = RebuildRunner()
    return _singleton
