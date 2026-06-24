"""磁盘空间预校验。

backup export 时检查 out_dir 所在卷剩余 >= data 大小 × 2.5（覆盖压缩与中间副本）。
不足时抛 InsufficientDiskSpaceError，由 API 层转 HTTP 507。
"""
from __future__ import annotations

import shutil
from typing import Optional


class InsufficientDiskSpaceError(RuntimeError):
    def __init__(self, required_bytes: int, available_bytes: int, target: str) -> None:
        super().__init__(
            f"insufficient disk space at {target}: "
            f"required={required_bytes} bytes, available={available_bytes} bytes"
        )
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes
        self.target = target


def require_disk_space(
    target_dir: str,
    required_bytes: Optional[int] = None,
    data_size_bytes: Optional[int] = None,
    safety_factor: float = 2.5,
) -> None:
    """校验 target_dir 所在卷可用空间。

    用法二选一：
      - 直接传 required_bytes（精确字节数）
      - 传 data_size_bytes + safety_factor（自动算 required = data_size × safety_factor）
    """
    if required_bytes is None:
        if data_size_bytes is None:
            raise ValueError("must provide required_bytes or data_size_bytes")
        required_bytes = int(data_size_bytes * safety_factor)
    usage = shutil.disk_usage(target_dir)
    if usage.free < required_bytes:
        raise InsufficientDiskSpaceError(
            required_bytes=required_bytes,
            available_bytes=usage.free,
            target=target_dir,
        )
