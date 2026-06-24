"""`.pre-restore.*` 残留检测与用户决策入口（审计 #7 / #8）。

import_overwrite 在内层防护阶段会写入 `.pre-restore.bak` / `.pre-restore-qdrant`，
正常完成后会清掉；但 kill -9 / 断电 / 进程崩溃不会进入 except / finally，
副本会残留，下次启动时服务无法自行判断当前 sqlite / qdrant 是"还原成功的新数据"
还是"还原中途崩了的半数据"。

本模块：
- 启动钩子 ``detect_and_warn``：发现残留 → WARNING 日志 + 置 maintenance flag，
  阻止任何写类请求直到用户显式决策
- ``execute_recover(action, ...)``：路由层调用，按 action 回滚或丢弃
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.services.maintenance import MaintenanceReason, get_maintenance_flag


logger = logging.getLogger(__name__)


PRE_DB_NAME = ".pre-restore.bak"
PRE_QDRANT_NAME = ".pre-restore-qdrant"


@dataclass
class PreRestoreState:
    """启动时探测到的 .pre-restore.* 状态快照。"""
    has_db: bool
    has_qdrant: bool
    db_path: Path
    qdrant_path: Path
    sqlite_path: Path
    qdrant_local_path: Path

    @property
    def has_any(self) -> bool:
        return self.has_db or self.has_qdrant


def probe(sqlite_path: str, qdrant_local_path: str) -> PreRestoreState:
    """检测 data 目录下是否有 .pre-restore 残留。"""
    sqlite = Path(sqlite_path)
    qdrant = Path(qdrant_local_path)
    data_dir = sqlite.parent
    pre_db = data_dir / PRE_DB_NAME
    pre_qdrant = data_dir / PRE_QDRANT_NAME
    return PreRestoreState(
        has_db=pre_db.exists(),
        has_qdrant=pre_qdrant.exists(),
        db_path=pre_db,
        qdrant_path=pre_qdrant,
        sqlite_path=sqlite,
        qdrant_local_path=qdrant,
    )


def detect_and_warn(sqlite_path: str, qdrant_local_path: str) -> Optional[PreRestoreState]:
    """启动钩子：探测残留，发现则警告 + 置 maintenance flag。

    flag 持续到用户调 POST /v1/system/recover/pre-restore 决策为止。
    """
    state = probe(sqlite_path, qdrant_local_path)
    if not state.has_any:
        return None
    logger.warning(
        "DETECTED .pre-restore residue at startup: db=%s qdrant=%s — "
        "前次 import_overwrite 未正常结束。服务进入只读维护模式，"
        "需要管理员通过 POST /v1/system/recover/pre-restore 选择回滚或丢弃。",
        state.has_db,
        state.has_qdrant,
    )
    flag = get_maintenance_flag()
    if not flag.is_active():
        flag.set(
            MaintenanceReason.PRE_RESTORE_STALE,
            detail=(
                f"pre-restore residue: db={state.has_db} qdrant={state.has_qdrant}; "
                f"call POST /v1/system/recover/pre-restore to decide"
            ),
        )
    return state


def execute_recover(
    action: str,
    sqlite_path: str,
    qdrant_local_path: str,
    on_vector_pause=None,
    on_vector_resume=None,
) -> dict:
    """处理 .pre-restore 残留。

    ``action="rollback"``：用 .pre-restore.* 覆盖当前 data，恢复 import 之前的状态
    ``action="discard"``：直接删除 .pre-restore.*，承认当前 data 是真相

    操作期间通过 on_vector_pause/resume 回调挂起 vector_index，防止 search 拿到
    stale handle。成功后清 maintenance flag。
    """
    if action not in ("rollback", "discard"):
        raise ValueError(f"unknown action: {action!r}")

    state = probe(sqlite_path, qdrant_local_path)
    if not state.has_any:
        # 已经被清掉了（也许 race condition），但要确保 flag 被清
        flag = get_maintenance_flag()
        if flag.reason() == MaintenanceReason.PRE_RESTORE_STALE:
            flag.clear()
        return {"ok": True, "action": action, "no_residue": True}

    if on_vector_pause:
        try:
            on_vector_pause()
        except Exception:
            logger.warning("recover: vector pause failed", exc_info=True)

    try:
        if action == "rollback":
            if state.has_db:
                shutil.copy2(state.db_path, state.sqlite_path)
                state.db_path.unlink()
            if state.has_qdrant:
                if state.qdrant_local_path.exists():
                    shutil.rmtree(state.qdrant_local_path)
                shutil.move(str(state.qdrant_path), str(state.qdrant_local_path))
        else:  # discard
            if state.has_db:
                state.db_path.unlink()
            if state.has_qdrant:
                shutil.rmtree(state.qdrant_path)
    finally:
        if on_vector_resume:
            try:
                on_vector_resume()
            except Exception:
                logger.warning("recover: vector resume failed", exc_info=True)

    flag = get_maintenance_flag()
    if flag.reason() == MaintenanceReason.PRE_RESTORE_STALE:
        flag.clear()

    logger.info(
        "op=pre_restore_recover action=%s db_handled=%s qdrant_handled=%s",
        action,
        state.has_db,
        state.has_qdrant,
    )
    return {
        "ok": True,
        "action": action,
        "db_handled": state.has_db,
        "qdrant_handled": state.has_qdrant,
    }
