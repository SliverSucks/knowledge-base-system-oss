"""直装版 FastAPI 服务入口，供 PyInstaller 打包为 kb-api.exe。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller --onefile 解压到临时目录，Windows 默认不搜索该目录的 DLL
# 必须在任何可能触发 _ctypes 的 import 之前注册
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(sys._MEIPASS)
    os.environ["PATH"] = sys._MEIPASS + os.pathsep + os.environ.get("PATH", "")

# PyInstaller --noconsole 模式下 sys.stdout/stderr 为 None
# uvicorn DefaultFormatter.__init__ 会调用 stream.isatty() 导致 AttributeError
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller 打包模式：sys.executable = <install_root>\bin\kb-api.exe
        return Path(sys.executable).parent.parent
    # 开发模式：app/server_entry.py 位于 <project_root>/app/
    return Path(__file__).parent.parent


def _load_config(root: Path) -> dict:
    cfg_path = root / "config" / "config.toml"
    if cfg_path.exists():
        with open(cfg_path, "rb") as f:
            return tomllib.load(f)
    return {}


def main() -> None:
    root = _install_root()
    cfg = _load_config(root)

    server_cfg = cfg.get("server", {})
    data_cfg = cfg.get("data", {})

    host: str = server_cfg.get("host", "127.0.0.1")
    port: int = int(server_cfg.get("port", 18000))

    sqlite_path = str(root / data_cfg.get("sqlite_path", "data/knowledge.db"))
    qdrant_path = str(root / data_cfg.get("qdrant_local_path", "data/qdrant_local"))
    vector_enabled = "1" if data_cfg.get("vector_enabled", True) else "0"

    os.environ.setdefault("KB_BACKEND", "sqlite")
    os.environ["SQLITE_PATH"] = sqlite_path
    os.environ["VECTOR_ENABLED"] = vector_enabled
    os.environ["QDRANT_MODE"] = "local"
    os.environ["QDRANT_LOCAL_PATH"] = qdrant_path

    print(f"[server-entry] KB_BACKEND={os.environ.get('KB_BACKEND', 'sqlite')}")
    print(f"[server-entry] PORT={port} (source=config.toml)")
    print(f"[server-entry] SQLITE_PATH={sqlite_path}")

    # 确保数据目录存在
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    Path(qdrant_path).mkdir(parents=True, exist_ok=True)

    import uvicorn
    from app.main import app as fastapi_app  # noqa: F401

    uvicorn.run(fastapi_app, host=host, port=port, workers=1)


if __name__ == "__main__":
    main()
