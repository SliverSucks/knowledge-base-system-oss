"""本地直启脚本：无需 Docker，自动建库、启动 uvicorn。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    # 切换到项目根目录
    os.chdir(ROOT)

    # 默认使用 SQLite 后端
    os.environ.setdefault("KB_BACKEND", "sqlite")
    os.environ.setdefault("SQLITE_PATH", str(ROOT / "data" / "knowledge.db"))
    os.environ.setdefault("VECTOR_ENABLED", "1")
    os.environ.setdefault("QDRANT_MODE", "local")
    os.environ.setdefault("QDRANT_LOCAL_PATH", str(ROOT / "data" / "qdrant_local"))

    # 确保 data 目录存在
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "qdrant_local").mkdir(exist_ok=True)

    backend = os.environ["KB_BACKEND"]
    sqlite_path = os.environ.get("SQLITE_PATH", "")
    print(f"[start-local] KB_BACKEND={backend}")
    if backend == "sqlite":
        print(f"[start-local] SQLITE_PATH={sqlite_path}")
    print(f"[start-local] QDRANT_MODE={os.environ.get('QDRANT_MODE', 'server')}")

    # QDRANT_MODE=local 要求单 worker，避免多进程并发写索引
    qdrant_mode = os.environ.get("QDRANT_MODE", "server").lower()
    workers = "1" if qdrant_mode == "local" else os.environ.get("UVICORN_WORKERS", "1")

    cmd = [
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", os.environ.get("KB_HOST", "0.0.0.0"),
        "--port", os.environ.get("KB_PORT", "18000"),
        "--workers", workers,
    ]

    print(f"[start-local] 启动命令: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
