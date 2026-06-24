"""内置 Embedding 服务（infinity）的安装计划与纯逻辑层。

职责边界（openspec embedded-embedding-service v1.2 §3.2 安装归属定死）：
本模块只做**纯业务 + 计划生成**，绝不执行下载 / 不 spawn 进程 / 不跑 pip——
这些动作全部由壳层（mac-app / windows-app 的 ProcessManager）执行。理由：壳层
天然持有进程能力 + venv 写权限，把"执行"集中到单一 owner，避免进度上报 / 文件
权限 / 路径 / 失败回滚在 kb-api 与壳层两侧打架。

关键约束：kb-api 的 .venv **没有装 torch**（torch 在独立的 embedding-service
venv 里），所以设备检测（torch.cuda.is_available）只能由壳层在 embedding venv
中执行；本模块仅生成"检测命令"并对结果做纯逻辑裁决（resolve_device）。

故本模块刻意不含 `download_model` / `subprocess` / `snapshot_download` 调用（AC27）。
"""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path

from app.services.disk_space import require_disk_space

# infinity 默认绑定端口（127.0.0.1，不暴露 0.0.0.0，AC15）。被占时自动 +1 避让。
DEFAULT_EMBEDDING_PORT = 7687
# pending chunk ≥ 此阈值时 reindex 才置 maintenance flag 挡写（202）；
# 小于则后台异步重建、允许边搜边写（v1.2 §4.5 写 API 阈值放行）。
REINDEX_MAINTENANCE_THRESHOLD = 5000
# 下载磁盘预检安全系数：模型大小 × 1.5（留出解压 / 临时文件余量）。
_MODEL_DISK_SAFETY_FACTOR = 1.5


@dataclass(frozen=True)
class ModelSpec:
    """内置可选 embedding 模型的元数据。

    size_bytes / ram_bytes 为近似值，仅用于磁盘预检与 UI 展示，非精确约束。
    """

    model_id: str          # HuggingFace repo id
    display_name: str
    dim: int               # 向量维度（决定切模型是否必然 reindex）
    size_bytes: int        # 模型文件近似总大小（磁盘预检用）
    ram_bytes: int         # 常驻内存近似占用（帮 8GB 设备避坑，UI 展示）
    multilingual: bool     # 是否多语言（中英混合 / 纯中文场景选型参考）


_GB = 1024 ** 3

# 内置可选模型注册表。默认推荐 bge-m3（多语言 + 长文本 + 1024 dim，KB 场景甜蜜点）。
MODEL_REGISTRY: dict[str, ModelSpec] = {
    "bge-m3": ModelSpec(
        model_id="BAAI/bge-m3",
        display_name="BGE-M3（多语言，推荐）",
        dim=1024,
        size_bytes=int(2.3 * _GB),
        ram_bytes=int(1.5 * _GB),
        multilingual=True,
    ),
    "bge-large-zh-v1.5": ModelSpec(
        model_id="BAAI/bge-large-zh-v1.5",
        display_name="BGE-large-zh v1.5（纯中文）",
        dim=1024,
        size_bytes=int(1.3 * _GB),
        ram_bytes=int(0.8 * _GB),
        multilingual=False,
    ),
    "qwen3-embedding-0.6b": ModelSpec(
        model_id="Qwen/Qwen3-Embedding-0.6B",
        display_name="Qwen3-Embedding 0.6B",
        dim=1024,
        size_bytes=int(1.2 * _GB),
        ram_bytes=int(0.8 * _GB),
        multilingual=True,
    ),
}

DEFAULT_MODEL_KEY = "bge-m3"

# 合法推理设备。默认 cpu——infinity-emb 检测到 GPU 会自动用 CUDA，未装 driver
# 的笔记本会直接启动失败，故必须显式传 device（v1.2 §4.7）。
VALID_DEVICES = ("cpu", "cuda", "mps")


class EmbeddingInstallError(RuntimeError):
    """安装计划 / 配置阶段的业务异常（区别于壳层执行期异常）。"""


@dataclass
class InstallPlan:
    """交给壳层执行的安装计划（命令字符串集合）。

    本模块只生成此计划，壳层（ProcessManager）负责实际执行：建 venv → pip 装
    infinity-emb → snapshot_download 下模型 → 起 infinity 进程。
    """

    model_spec: ModelSpec
    venv_dir: str                       # embedding-service/venv 绝对路径
    model_dir: str                      # models/{key} 绝对路径
    device: str
    port: int                           # infinity 绑定端口（壳层探活 /health 用同一个）
    create_venv_cmd: list[str]          # 建独立 venv
    pip_install_cmd: list[str]          # 装 infinity-emb[server,torch] + 升级 pip
    download_args: dict[str, str]       # snapshot_download 参数（壳层据此下载）
    start_cmd: list[str]                # 启动 infinity 子进程
    env: dict[str, str] = field(default_factory=dict)  # 启动 infinity 时必须注入的 env（如 INFINITY_BETTERTRANSFORMER=false）
    device_detect_cmd: list[str] = field(default_factory=list)  # 壳层在 venv 内探测 GPU


def resolve_model(model_key: str) -> ModelSpec:
    """把 UI 传入的 model_key 解析为 ModelSpec，未知 key 抛业务异常。"""
    spec = MODEL_REGISTRY.get(model_key)
    if spec is None:
        raise EmbeddingInstallError(
            f"未知模型 key: {model_key}；可选：{', '.join(MODEL_REGISTRY)}"
        )
    return spec


def resolve_device(configured: str | None, detected_cuda: bool | None = None) -> str:
    """裁决最终推理设备（纯逻辑，不 import torch）。

    优先级：用户显式配置 > 壳层回传的 GPU 探测结果 > cpu 兜底。
    configured 非法值直接报错，避免把脏值塞进 infinity 启动命令。
    """
    if configured:
        if configured not in VALID_DEVICES:
            raise EmbeddingInstallError(
                f"非法 device: {configured}；可选：{', '.join(VALID_DEVICES)}"
            )
        return configured
    if detected_cuda:
        return "cuda"
    return "cpu"


def require_model_disk_space(model_key: str, target_dir: str) -> None:
    """下载前磁盘预检：复用 disk_space.require_disk_space，模型大小 × 1.5。

    不足时抛 InsufficientDiskSpaceError（由 API 层转 HTTP 507）。
    """
    spec = resolve_model(model_key)
    require_disk_space(
        target_dir=target_dir,
        required_bytes=int(spec.size_bytes * _MODEL_DISK_SAFETY_FACTOR),
    )


def find_free_port(start_port: int = DEFAULT_EMBEDDING_PORT, host: str = "127.0.0.1",
                   max_tries: int = 64) -> int:
    """从 start_port 起探测第一个空闲端口（bind 测试，纯 IO 无 torch 依赖）。

    注意 TOCTOU：本函数只保证调用瞬间空闲，壳层真正启动 infinity 前应再次确认；
    最终监听端口以壳层写入 runtime/port 的实际值为准。
    """
    for offset in range(max_tries):
        port = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise EmbeddingInstallError(
        f"在 {start_port}~{start_port + max_tries - 1} 未找到空闲端口"
    )


def is_owned_infinity(cmdline: str, port: int, model_id: str) -> bool:
    """owner 凭证判定（纯函数）：cmdline 是否为本应用拉起的目标 infinity（AC25 / §4.4）。

    壳层清残留 / 复用端口时调用：读到某 PID 的 cmdline 后用本函数判断是否"自己人"，
    不匹配一律视为外人进程，只换端口绝不杀（避免误杀用户其他程序）。

    匹配规则：cmdline 同时包含目标 port 与 model_id 的 infinity 启动特征。
    """
    if not cmdline:
        return False
    needle_port = f"--port {port}"
    needle_model = f"--model-id {model_id}"
    return "infinity" in cmdline and needle_port in cmdline and needle_model in cmdline


def should_block_writes_for_reindex(pending_chunk_count: int) -> bool:
    """reindex 是否需要置 maintenance flag 挡写（v1.2 §4.5 阈值放行）。

    ≥ 阈值：大库重建耗时长，置 flag 挡写（写 API 返 202 + Retry-After）。
    < 阈值：小库后台异步重建，允许用户继续边搜边写。
    """
    return pending_chunk_count >= REINDEX_MAINTENANCE_THRESHOLD


def build_install_plan(
    model_key: str,
    data_root: str,
    *,
    device: str | None = None,
    detected_cuda: bool | None = None,
    mirror: str | None = "https://hf-mirror.com",
) -> InstallPlan:
    """生成交给壳层执行的安装计划（不执行任何下载 / 进程动作）。

    data_root 下布局：embedding-service/venv、models/{key}（与 design §3.1 一致）。
    device 留空时按 resolve_device 裁决（壳层探测结果 / cpu 兜底）。
    """
    spec = resolve_model(model_key)
    resolved_device = resolve_device(device, detected_cuda=detected_cuda)

    root = Path(data_root)
    venv_dir = root / "embedding-service" / "venv"
    model_dir = root / "models" / model_key

    # 壳层平台差异（venv/bin vs venv/Scripts）由壳层按自身平台拼接；此处给出
    # 逻辑入口名，壳层负责映射到 bin/python 或 Scripts/python.exe。
    venv_python = str(venv_dir / "bin" / "python")
    venv_pip = str(venv_dir / "bin" / "pip")
    venv_infinity = str(venv_dir / "bin" / "infinity_emb")

    return InstallPlan(
        model_spec=spec,
        venv_dir=str(venv_dir),
        model_dir=str(model_dir),
        device=resolved_device,
        port=DEFAULT_EMBEDDING_PORT,
        create_venv_cmd=["python", "-m", "venv", str(venv_dir)],
        # 装 [server,torch]（双 extras 实测够用，全套踩坑见下）+ 升级 pip：
        #   [server]   v2 启动需要的 FastAPI + uvicorn
        #   [torch]    torch / sentence-transformers（infinity-emb 0.0.77 主依赖
        #              只有 numpy + huggingface_hub，torch 是 optional）
        # 不装 [optimum]：pip 21 装 [optimum] backtrack 45 分钟 + optimum 2.0
        #   移除 bettertransformer + optimum 1.x 又跟 transformers 4.49+ 不兼容
        #   = 版本地狱。改用 env INFINITY_BETTERTRANSFORMER=false 关掉
        #   BetterTransformer 探测（acceleration.py:36 第一行直接 return False，
        #   根本不走 optimum 代码）—— env 在 plan.env 里下发给 Swift StartHandler。
        # 避开 [all]：vision/ct2/audio/tensorrt/onnxruntime-gpu 全拉触发
        #   pip resolver backtrack 几十分钟（1.3.5 实测踩过）。
        # huggingface_hub<1.0：infinity-emb 代码 `from huggingface_hub import
        #   HfFolder`，hf_hub 1.0+ 移除该 API。pin 避开 ImportError。
        # /bin/sh -c 串两步：先升级 pip（venv 默认 pip 21.2.4 resolver 太旧），
        #   再用新 pip 装 infinity-emb。两条独立命令，避免 race。
        pip_install_cmd=[
            "/bin/sh", "-c",
            f"{venv_python} -m pip install --upgrade pip && "
            f"{venv_python} -m pip install 'infinity-emb[server,torch]' 'huggingface_hub<1.0'",
        ],
        download_args={
            "repo_id": spec.model_id,
            "local_dir": str(model_dir),
            "endpoint": mirror or "",
        },
        # infinity-emb v2 启动模板；壳层实施时按实际 infinity-emb 版本核对参数名。
        # --port 显式传 DEFAULT_EMBEDDING_PORT（7687），不然 infinity 用自己默认
        # 7997 → Swift 端按 plan port 探活 /health 永远撞不到，warmup 必 timeout。
        start_cmd=[
            venv_infinity, "v2",
            "--model-id", str(model_dir),
            "--host", "127.0.0.1",
            "--port", str(DEFAULT_EMBEDDING_PORT),
            "--device", resolved_device,
            "--model-warmup",
        ],
        # INFINITY_BETTERTRANSFORMER=false 关掉 BetterTransformer 探测，绕开
        # acceleration.py:46 引用未定义的 BetterTransformerManager 的 NameError
        # （详见 pip_install_cmd 注释）。
        env={"INFINITY_BETTERTRANSFORMER": "false"},
        device_detect_cmd=[
            venv_python, "-c",
            "import torch; print(torch.cuda.is_available())",
        ],
    )
