#!/usr/bin/env python3
"""Fake infinity-emb：Phase 3 ProcessManager 集成测试用 stub。

模拟真 infinity-emb 的可观察行为，**不**加载任何模型；用 hash 函数返
回固定维度向量。仅用于壳层 ProcessManager（mac-app / windows-app）的
端到端验证：

- AC14a：``--sigterm-mode ignore`` 验证强杀路径（3 秒内 SIGKILL）
- AC14b：cmdline 含 ``infinity --port {port} --model-id {model_id}``
  特征，``is_owned_infinity()`` 判定 = True；用于"残留识别"测试
- AC19：``--warmup-seconds`` 控制 warming_up 持续时长，验证 202 退场
- AC24：分级就绪——本进程崩或慢起不应阻塞 kb-api 主服务

启动行为（仅 stdlib，不依赖第三方包，跨 mac/win 直接跑）：

    fake_infinity v2 \\
        --port 7687 \\
        --model-id bge-m3 \\
        --host 127.0.0.1 \\
        --device cpu \\
        --warmup-seconds 2 \\
        --sigterm-mode normal

端点：

- ``GET  /health``      → 200 OK，``{"status":"ok"}``，warming 期间返 503
- ``POST /v1/embeddings`` → 200 OK，返回固定维度 hash 向量

子命令 ``v2`` 是 infinity-emb v2 的位置参数；保留以让 cmdline 跟真
infinity 完全一致（``is_owned_infinity`` 检查 "infinity" 子串即过）。
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import signal
import socketserver
import sys
import threading
import time
from typing import Any


# ---------------------------------------------------------------------------
# 全局状态（单进程 stub，简单粗暴）
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "warming_up": True,
    "model_id": "",
    "device": "cpu",
    "started_at": 0.0,
}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    # 关掉默认 access log，避免污染 ProcessManager 日志收集
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            if _state["warming_up"]:
                self._send_json(503, {"status": "warming"})
            else:
                self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/embeddings":
            self._send_json(404, {"detail": "not found"})
            return
        if _state["warming_up"]:
            self._send_json(503, {"status": "warming"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"detail": "bad json"})
            return

        inputs = payload.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        dim = 16
        data = []
        for idx, text in enumerate(inputs):
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            # 把 sha256 折叠成固定维度浮点向量；纯 hash，不携带语义
            vec = [
                ((digest[i % len(digest)] - 128) / 128.0) for i in range(dim)
            ]
            data.append({"object": "embedding", "index": idx, "embedding": vec})

        self._send_json(200, {
            "object": "list",
            "data": data,
            "model": _state["model_id"],
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        })


# ---------------------------------------------------------------------------
# Warming up + 信号处理
# ---------------------------------------------------------------------------


def _warmup(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
    _state["warming_up"] = False


def _install_signal_handlers(mode: str, server: socketserver.BaseServer) -> None:
    """SIGTERM 模式：

    - ``normal``：收到立即关闭 server，正常退出 0
    - ``ignore``：忽略 SIGTERM，验证壳层 3 秒后用 SIGKILL（AC14a）
    - ``delayed``：收到后 sleep 10s 再退出，验证强杀超时路径
    """
    if mode == "normal":
        def _handler(signum: int, frame: Any) -> None:
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, _handler)
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, _handler)
    elif mode == "ignore":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    elif mode == "delayed":
        def _handler(signum: int, frame: Any) -> None:
            time.sleep(10)
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, _handler)
    else:
        raise SystemExit(f"unknown --sigterm-mode: {mode}")


def _maybe_schedule_crash(crash_after: float) -> None:
    """``--crash-after`` 秒后用 os._exit(137) 强退，模拟 OOM 等异常死亡。"""
    if crash_after <= 0:
        return

    def _crash() -> None:
        time.sleep(crash_after)
        os._exit(137)

    threading.Thread(target=_crash, daemon=True).start()


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="infinity_emb",  # 让 cmdline 含 "infinity" 满足 is_owned_infinity
    )
    # 兼容 infinity-emb v2 的子命令位置参数
    parser.add_argument("subcommand", nargs="?", default="v2")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--model-id", required=True, dest="model_id")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--model-warmup", action="store_true")

    # fake_infinity 独有控制参数
    parser.add_argument(
        "--warmup-seconds", type=float, default=0.5,
        help="模拟模型加载耗时，期间 /health 返 503、/v1/embeddings 返 503",
    )
    parser.add_argument(
        "--sigterm-mode", choices=["normal", "ignore", "delayed"], default="normal",
        help="测试 AC14a 强杀路径：ignore=忽略 SIGTERM，delayed=10s 后才退",
    )
    parser.add_argument(
        "--crash-after", type=float, default=0.0,
        help="N 秒后 os._exit(137) 强退，模拟 OOM / 段错误异常死亡",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    _state["model_id"] = args.model_id
    _state["device"] = args.device
    _state["started_at"] = time.time()
    _state["warming_up"] = True

    # 不允许 0.0.0.0 监听（AC15）
    if args.host != "127.0.0.1":
        print(f"fake_infinity refuses to bind {args.host} (only 127.0.0.1)", file=sys.stderr)
        sys.exit(2)

    httpd = http.server.ThreadingHTTPServer((args.host, args.port), _Handler)
    _install_signal_handlers(args.sigterm_mode, httpd)
    _maybe_schedule_crash(args.crash_after)

    threading.Thread(target=_warmup, args=(args.warmup_seconds,), daemon=True).start()

    # 启动信号给壳层：固定一行，便于壳层 readiness gate 抓
    print(
        f"fake_infinity listening on http://{args.host}:{args.port} "
        f"model={args.model_id} device={args.device}",
        flush=True,
    )

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
