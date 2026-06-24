from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _allow_tmpdir_in_kb_data_roots(monkeypatch):
    """生产环境 _allowed_data_roots 不再默认包含 $TMPDIR（审计 #12 二次收紧），
    但测试在 pytest tmp_path / 其他 tmp 下创建 SQLite/qdrant，需要白名单注入。
    每个测试都通过 monkeypatch 临时把 tempfile.gettempdir() 加入 KB_DATA_ROOTS。
    """
    existing = monkeypatch.getenv("KB_DATA_ROOTS") if hasattr(monkeypatch, "getenv") else None
    # 简单：直接覆盖（测试不会依赖外部 KB_DATA_ROOTS）
    monkeypatch.setenv("KB_DATA_ROOTS", tempfile.gettempdir())
