"""磁盘空间预校验测试。"""
from __future__ import annotations

from collections import namedtuple

import pytest


_FakeUsage = namedtuple("DiskUsage", "total used free")


def test_passes_when_enough_space(tmp_path):
    from app.services.disk_space import require_disk_space
    require_disk_space(target_dir=str(tmp_path), required_bytes=1024)


def test_raises_when_insufficient(monkeypatch, tmp_path):
    import shutil
    from app.services.disk_space import InsufficientDiskSpaceError, require_disk_space

    monkeypatch.setattr(shutil, "disk_usage", lambda p: _FakeUsage(1_000_000, 800_000, 200_000))

    with pytest.raises(InsufficientDiskSpaceError) as exc:
        require_disk_space(target_dir=str(tmp_path), required_bytes=500_000)
    msg = str(exc.value)
    assert "500000" in msg or "500_000" in msg
    assert "200000" in msg or "available" in msg.lower()


def test_safety_factor_default_2_5(monkeypatch, tmp_path):
    """data 500MB → require 500*2.5=1250 MB；可用 1GB 不够。"""
    import shutil
    from app.services.disk_space import InsufficientDiskSpaceError, require_disk_space

    monkeypatch.setattr(shutil, "disk_usage", lambda p: _FakeUsage(2_000_000_000, 0, 1_000_000_000))

    with pytest.raises(InsufficientDiskSpaceError):
        require_disk_space(target_dir=str(tmp_path), data_size_bytes=500_000_000, safety_factor=2.5)

    # 改 safety_factor=1.5 → 750MB 需要，1GB 够用
    require_disk_space(target_dir=str(tmp_path), data_size_bytes=500_000_000, safety_factor=1.5)


def test_raises_value_error_without_size_inputs(tmp_path):
    """既未传 required_bytes 也未传 data_size_bytes 时拒绝。"""
    from app.services.disk_space import require_disk_space
    with pytest.raises(ValueError, match="required_bytes or data_size_bytes"):
        require_disk_space(target_dir=str(tmp_path))


def test_error_exposes_required_and_available(monkeypatch, tmp_path):
    """异常对象保留 required/available/target 字段，便于 API 层组装响应。"""
    import shutil
    from app.services.disk_space import InsufficientDiskSpaceError, require_disk_space

    monkeypatch.setattr(shutil, "disk_usage", lambda p: _FakeUsage(100, 80, 20))
    try:
        require_disk_space(target_dir=str(tmp_path), required_bytes=50)
    except InsufficientDiskSpaceError as e:
        assert e.required_bytes == 50
        assert e.available_bytes == 20
        assert e.target == str(tmp_path)
    else:
        raise AssertionError("应抛 InsufficientDiskSpaceError")
