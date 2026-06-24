"""进程级维护模式 flag（审计 #1）。

写类 API（upsert / DELETE / reindex / import）在 flag 置位时返回 503 + Retry-After: 60。
只读 API（search / get / health / config GET）不受影响。

线程安全：当前直装版 uvicorn 单 worker 单进程，threading.Lock 已足够；
未来如多 worker 部署需迁移到共享存储（如 Redis），届时只需重写本模块接口。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MaintenanceReason(str, Enum):
    BACKUP_IMPORT = "backup_import"
    PRE_RESTORE_STALE = "pre_restore_stale"
    # 切模型 / 手动重建向量索引：仅当 pending chunk ≥ REINDEX_MAINTENANCE_THRESHOLD
    # 才置位（小库走后台异步不锁，见 embedding_install.should_block_writes_for_reindex）。
    REINDEX = "reindex"


@dataclass
class _State:
    active: bool = False
    reason: Optional[MaintenanceReason] = None
    detail: str = ""


class MaintenanceFlag:
    """进程级 maintenance flag。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = _State()

    def is_active(self) -> bool:
        with self._lock:
            return self._state.active

    def reason(self) -> Optional[MaintenanceReason]:
        with self._lock:
            return self._state.reason

    def detail(self) -> str:
        with self._lock:
            return self._state.detail

    def set(self, reason: MaintenanceReason, detail: str = "") -> None:
        with self._lock:
            if self._state.active:
                raise RuntimeError(
                    f"maintenance flag already active "
                    f"(reason={self._state.reason.value if self._state.reason else 'unknown'}); "
                    f"refuse to overwrite"
                )
            self._state = _State(active=True, reason=reason, detail=detail)

    def clear(self) -> None:
        with self._lock:
            self._state = _State()


_singleton: Optional[MaintenanceFlag] = None
_singleton_lock = threading.Lock()


def get_maintenance_flag() -> MaintenanceFlag:
    """返回进程级 MaintenanceFlag 单例。"""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = MaintenanceFlag()
    return _singleton
