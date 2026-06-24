"""二次确认 token 校验测试。"""
from __future__ import annotations

import pytest


def test_overwrite_token_accepted():
    from app.services.confirm_token import require_confirm_token
    # 不抛即视为通过
    require_confirm_token(mode="overwrite", confirm="I-CONFIRM-OVERWRITE")


def test_merge_token_accepted():
    from app.services.confirm_token import require_confirm_token
    require_confirm_token(mode="merge", confirm="I-CONFIRM-MERGE")


def test_rollback_token_accepted():
    from app.services.confirm_token import require_confirm_token
    require_confirm_token(mode="rollback", confirm="I-CONFIRM-ROLLBACK")


def test_discard_token_accepted():
    from app.services.confirm_token import require_confirm_token
    require_confirm_token(mode="discard", confirm="I-CONFIRM-DISCARD")


def test_missing_confirm_rejected():
    from app.services.confirm_token import ConfirmTokenError, require_confirm_token
    with pytest.raises(ConfirmTokenError, match="confirm"):
        require_confirm_token(mode="overwrite", confirm=None)
    with pytest.raises(ConfirmTokenError, match="confirm"):
        require_confirm_token(mode="overwrite", confirm="")


def test_weak_confirm_rejected():
    from app.services.confirm_token import ConfirmTokenError, require_confirm_token
    for weak in ("true", "yes", "1", "ok", "True", "y"):
        with pytest.raises(ConfirmTokenError):
            require_confirm_token(mode="overwrite", confirm=weak)


def test_mismatched_mode_rejected():
    from app.services.confirm_token import ConfirmTokenError, require_confirm_token
    with pytest.raises(ConfirmTokenError, match="does not match mode"):
        require_confirm_token(mode="merge", confirm="I-CONFIRM-OVERWRITE")
    with pytest.raises(ConfirmTokenError, match="does not match mode"):
        require_confirm_token(mode="overwrite", confirm="I-CONFIRM-MERGE")


def test_unknown_mode_rejected():
    from app.services.confirm_token import ConfirmTokenError, require_confirm_token
    with pytest.raises(ConfirmTokenError, match="unknown mode"):
        require_confirm_token(mode="something", confirm="I-CONFIRM-SOMETHING")


def test_case_sensitive():
    """token 必须严格区分大小写。"""
    from app.services.confirm_token import ConfirmTokenError, require_confirm_token
    with pytest.raises(ConfirmTokenError):
        require_confirm_token(mode="overwrite", confirm="i-confirm-overwrite")
