"""二次确认 token 校验。

使用语义化 token（I-CONFIRM-OVERWRITE / I-CONFIRM-MERGE 等）
而非 bool confirm=true，要求调用方在客户端真实输入对应字符串才能放行，
防止 CSRF / 误传 / 客户端自动重放场景误触发破坏性操作。

用法（FastAPI 路由内）：
    try:
        require_confirm_token(mode=req.mode, confirm=req.confirm)
    except ConfirmTokenError as e:
        raise HTTPException(status_code=400, detail=str(e))
"""
from __future__ import annotations

from typing import Optional


_VALID = {
    "overwrite": "I-CONFIRM-OVERWRITE",
    "merge": "I-CONFIRM-MERGE",
    "rollback": "I-CONFIRM-ROLLBACK",
    "discard": "I-CONFIRM-DISCARD",
    # 改 embedding mode / 模型 ID 会触发全库 reindex（dim/向量空间变），强制确认
    "reindex": "I-CONFIRM-REINDEX",
}


class ConfirmTokenError(ValueError):
    """二次确认 token 校验失败。"""


def require_confirm_token(mode: str, confirm: Optional[str]) -> None:
    """严格校验二次确认 token；失败抛 ConfirmTokenError。

    - mode 必须在 _VALID 内
    - confirm 必须严格等于（区分大小写）对应 token
    """
    if mode not in _VALID:
        raise ConfirmTokenError(
            f"unknown mode '{mode}'; expected one of {sorted(_VALID.keys())}"
        )
    if not confirm:
        raise ConfirmTokenError(
            f"operation requires confirm token. Use exact string '{_VALID[mode]}'"
        )
    expected = _VALID[mode]
    if confirm != expected:
        if confirm.startswith("I-CONFIRM-"):
            raise ConfirmTokenError(
                f"confirm token '{confirm}' does not match mode '{mode}'; "
                f"expected '{expected}'"
            )
        raise ConfirmTokenError(
            f"confirm token must be exact string '{expected}', got '{confirm}'"
        )
