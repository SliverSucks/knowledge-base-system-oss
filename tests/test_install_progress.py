"""``InstallSseStreamer`` 单元测试（design v1.2 §3.2 + AC21 + AC26）。

通过注入 fake clock / sleep，不真等 200ms / 15s，验证：

- 文件不存在时只发心跳，不崩
- ``install_status.json`` mtime 变化 → 整段 emit
- ``phase=completed|failed`` → 立即结束
- ``pip.log`` size 增长 → 增量 emit 新追加内容
- 长时间无事件 → ≥15s 必有 keepalive（AC21）
- 超 ``max_duration_sec`` → emit timeout 后结束
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.install_progress import InstallSseStreamer, _format_sse


class FakeClock:
    """单调推进的假时钟；每次调用 ``sleep`` 把指针前推。"""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _parse_event(frame: str) -> tuple[str, dict]:
    """从 SSE 帧 ``event: X\\ndata: {...}\\n\\n`` 解出 (event, data)。"""
    lines = frame.strip().split("\n")
    assert lines[0].startswith("event: ")
    assert lines[1].startswith("data: ")
    return lines[0][len("event: "):], json.loads(lines[1][len("data: "):])


def _write_status(path: Path, payload: dict, *, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))


# ---------------------------------------------------------------------------
# 终止条件 / 边界
# ---------------------------------------------------------------------------

def test_timeout_when_no_files_exist(tmp_path):
    clock = FakeClock()
    streamer = InstallSseStreamer(
        status_path=tmp_path / "missing.json",
        pip_log_path=tmp_path / "missing.log",
        heartbeat_sec=0.3,
        tail_interval_sec=0.2,
        max_duration_sec=2.0,
        clock=clock,
        sleep=clock.sleep,
    )
    frames = list(streamer.events())
    # 没文件时只发心跳 + 最后一个 timeout
    assert frames[-1].startswith("event: timeout\n")
    assert any(f.startswith("event: keepalive\n") for f in frames)


def test_initial_snapshot_emitted_if_status_exists(tmp_path):
    status = tmp_path / "install_status.json"
    _write_status(status, {"phase": "downloading", "progress": 0.1})

    clock = FakeClock()
    streamer = InstallSseStreamer(
        status_path=status,
        pip_log_path=tmp_path / "pip.log",
        heartbeat_sec=5.0,
        tail_interval_sec=0.2,
        max_duration_sec=1.0,
        clock=clock,
        sleep=clock.sleep,
    )
    frames = list(streamer.events())
    name, data = _parse_event(frames[0])
    assert name == "status"
    assert data == {"phase": "downloading", "progress": 0.1}


def test_completed_phase_terminates_immediately(tmp_path):
    status = tmp_path / "install_status.json"
    _write_status(status, {"phase": "completed"})

    clock = FakeClock()
    streamer = InstallSseStreamer(
        status_path=status,
        pip_log_path=tmp_path / "pip.log",
        clock=clock,
        sleep=clock.sleep,
        max_duration_sec=600.0,
    )
    frames = list(streamer.events())
    assert len(frames) == 1
    assert _parse_event(frames[0])[0] == "status"
    # 一帧后立即终止，没进入主循环 sleep
    assert clock.sleeps == []


def test_failed_phase_terminates_immediately(tmp_path):
    status = tmp_path / "install_status.json"
    _write_status(status, {"phase": "failed", "error": "disk full"})

    clock = FakeClock()
    streamer = InstallSseStreamer(
        status_path=status,
        pip_log_path=tmp_path / "pip.log",
        clock=clock,
        sleep=clock.sleep,
    )
    frames = list(streamer.events())
    assert len(frames) == 1
    _, data = _parse_event(frames[0])
    assert data["phase"] == "failed"


# ---------------------------------------------------------------------------
# 心跳 / 增量 tail
# ---------------------------------------------------------------------------

def test_keepalive_when_no_changes(tmp_path):
    """无文件变化时 ≥ heartbeat_sec 必有 keepalive（AC21）。"""
    clock = FakeClock()
    streamer = InstallSseStreamer(
        status_path=tmp_path / "missing.json",
        pip_log_path=tmp_path / "missing.log",
        heartbeat_sec=15.0,
        tail_interval_sec=5.0,  # 加大 tick，3 次轮询触发心跳
        max_duration_sec=60.0,
        clock=clock,
        sleep=clock.sleep,
    )
    frames = list(streamer.events())
    keepalives = [f for f in frames if f.startswith("event: keepalive\n")]
    assert keepalives, "expected at least one keepalive within heartbeat_sec"


def test_pip_log_growth_emits_incremental_chunk(tmp_path):
    status = tmp_path / "install_status.json"
    pip_log = tmp_path / "pip.log"
    _write_status(status, {"phase": "installing"})
    pip_log.parent.mkdir(parents=True, exist_ok=True)
    pip_log.write_bytes(b"line1\n")

    clock = FakeClock()
    streamer = InstallSseStreamer(
        status_path=status,
        pip_log_path=pip_log,
        heartbeat_sec=100.0,
        tail_interval_sec=1.0,
        max_duration_sec=10.0,
        clock=clock,
        sleep=clock.sleep,
    )

    gen = streamer.events()
    # 第一帧 = 初始 status 快照
    first = next(gen)
    assert _parse_event(first)[0] == "status"

    # 在主循环 sleep 推进期间往 pip_log 追加
    # 由于生成器是惰性的，下一次 next() 才会进入主循环
    # 在 next 之前先追加内容
    with pip_log.open("ab") as fp:
        fp.write(b"line2\nline3\n")

    second = next(gen)
    name, data = _parse_event(second)
    assert name == "pip_log"
    assert data["chunk"] == "line2\nline3\n"


def test_status_mtime_change_emits_update(tmp_path):
    status = tmp_path / "install_status.json"
    _write_status(status, {"phase": "downloading", "progress": 0.1}, mtime=1000.0)

    clock = FakeClock()
    streamer = InstallSseStreamer(
        status_path=status,
        pip_log_path=tmp_path / "pip.log",
        heartbeat_sec=100.0,
        tail_interval_sec=1.0,
        max_duration_sec=10.0,
        clock=clock,
        sleep=clock.sleep,
    )

    gen = streamer.events()
    # 初始快照
    name, data = _parse_event(next(gen))
    assert data["progress"] == 0.1

    # 模拟壳层覆盖式 flush 新进度（新 mtime）
    _write_status(status, {"phase": "downloading", "progress": 0.5}, mtime=2000.0)

    name, data = _parse_event(next(gen))
    assert name == "status"
    assert data["progress"] == 0.5


def test_corrupt_status_json_skipped(tmp_path):
    """壳层覆盖式写期间读到半截 JSON 时本轮静默跳过，不应崩。"""
    status = tmp_path / "install_status.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text("{ this is not valid json")

    clock = FakeClock()
    streamer = InstallSseStreamer(
        status_path=status,
        pip_log_path=tmp_path / "pip.log",
        heartbeat_sec=5.0,
        tail_interval_sec=1.0,
        max_duration_sec=2.0,
        clock=clock,
        sleep=clock.sleep,
    )
    frames = list(streamer.events())
    # 不抛异常；最终走到 timeout
    assert any(f.startswith("event: timeout\n") for f in frames)


def test_format_sse_serializes_unicode(tmp_path):
    """SSE 帧默认保留中文（ensure_ascii=False），别变 \\uXXXX。"""
    frame = _format_sse("status", {"message": "下载中"})
    assert "下载中" in frame


# ---------------------------------------------------------------------------
# resolve_install_paths
# ---------------------------------------------------------------------------

def test_resolve_install_paths_lays_out_runtime_and_logs(tmp_path):
    from app.services.install_progress import resolve_install_paths
    status, pip_log = resolve_install_paths(str(tmp_path))
    assert status == tmp_path / "runtime" / "install_status.json"
    assert pip_log == tmp_path / "logs" / "pip.log"
