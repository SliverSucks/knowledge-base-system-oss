"""验证内置 Embedding 服务安装计划与纯逻辑层（app/services/embedding_install）。

覆盖：模型解析 / 设备裁决 / 端口探测 / owner 凭证判定 / reindex 阈值 /
磁盘预检 / 安装计划生成。本层刻意不含下载与 spawn（AC27），故全部可单测。
"""
from __future__ import annotations

import socket

import pytest

from app.services import embedding_install as ei
from app.services.disk_space import InsufficientDiskSpaceError


class TestResolveModel:
    def test_default_model_in_registry(self) -> None:
        assert ei.DEFAULT_MODEL_KEY in ei.MODEL_REGISTRY
        spec = ei.resolve_model(ei.DEFAULT_MODEL_KEY)
        assert spec.model_id == "BAAI/bge-m3"
        assert spec.dim == 1024

    def test_unknown_model_raises(self) -> None:
        with pytest.raises(ei.EmbeddingInstallError):
            ei.resolve_model("does-not-exist")


class TestResolveDevice:
    def test_configured_wins(self) -> None:
        assert ei.resolve_device("cuda") == "cuda"
        assert ei.resolve_device("mps", detected_cuda=False) == "mps"

    def test_invalid_configured_raises(self) -> None:
        with pytest.raises(ei.EmbeddingInstallError):
            ei.resolve_device("tpu")

    def test_detected_cuda_used_when_unconfigured(self) -> None:
        assert ei.resolve_device(None, detected_cuda=True) == "cuda"

    def test_cpu_fallback(self) -> None:
        assert ei.resolve_device(None, detected_cuda=False) == "cpu"
        assert ei.resolve_device(None) == "cpu"


class TestFindFreePort:
    def test_returns_start_port_when_free(self) -> None:
        # 先占一个端口，确认 find_free_port 会避让到下一个。
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            occupied.bind(("127.0.0.1", 0))
            taken = occupied.getsockname()[1]
            port = ei.find_free_port(start_port=taken)
            assert port != taken
            assert port > taken

    def test_exhausted_raises(self) -> None:
        with pytest.raises(ei.EmbeddingInstallError):
            # max_tries=0 → 无可尝试范围，立即耗尽。
            ei.find_free_port(start_port=7687, max_tries=0)


class TestIsOwnedInfinity:
    def test_match(self) -> None:
        cmd = "/x/venv/bin/infinity_emb v2 --model-id /d/models/bge-m3 --port 7687 --device cpu"
        assert ei.is_owned_infinity(cmd, port=7687, model_id="/d/models/bge-m3") is True

    def test_wrong_port_rejected(self) -> None:
        cmd = "infinity_emb v2 --model-id /d/models/bge-m3 --port 9999 --device cpu"
        assert ei.is_owned_infinity(cmd, port=7687, model_id="/d/models/bge-m3") is False

    def test_wrong_model_rejected(self) -> None:
        cmd = "infinity_emb v2 --model-id /d/models/other --port 7687 --device cpu"
        assert ei.is_owned_infinity(cmd, port=7687, model_id="/d/models/bge-m3") is False

    def test_foreign_process_rejected(self) -> None:
        assert ei.is_owned_infinity("python -m http.server 7687", port=7687, model_id="x") is False

    def test_empty_cmdline(self) -> None:
        assert ei.is_owned_infinity("", port=7687, model_id="x") is False


class TestReindexThreshold:
    def test_below_threshold_no_block(self) -> None:
        assert ei.should_block_writes_for_reindex(ei.REINDEX_MAINTENANCE_THRESHOLD - 1) is False

    def test_at_threshold_blocks(self) -> None:
        assert ei.should_block_writes_for_reindex(ei.REINDEX_MAINTENANCE_THRESHOLD) is True

    def test_above_threshold_blocks(self) -> None:
        assert ei.should_block_writes_for_reindex(ei.REINDEX_MAINTENANCE_THRESHOLD + 10000) is True


class TestRequireModelDiskSpace:
    def test_enough_space_passes(self, tmp_path) -> None:
        # tmp_path 所在卷正常有几 GB 空闲，bge-m3 ×1.5 ≈ 3.5GB 一般够。
        # 若 CI 卷过小会抛 InsufficientDiskSpaceError，属真实环境约束非逻辑错。
        try:
            ei.require_model_disk_space(ei.DEFAULT_MODEL_KEY, str(tmp_path))
        except InsufficientDiskSpaceError:
            pytest.skip("测试卷剩余空间不足，跳过（非逻辑错误）")

    def test_unknown_model_raises(self, tmp_path) -> None:
        with pytest.raises(ei.EmbeddingInstallError):
            ei.require_model_disk_space("nope", str(tmp_path))


class TestBuildInstallPlan:
    def test_plan_paths_and_device(self, tmp_path) -> None:
        plan = ei.build_install_plan("bge-m3", str(tmp_path), device="cpu")
        assert plan.model_spec.model_id == "BAAI/bge-m3"
        assert plan.device == "cpu"
        assert plan.venv_dir.endswith("embedding-service/venv")
        assert plan.model_dir.endswith("models/bge-m3")
        assert plan.download_args["repo_id"] == "BAAI/bge-m3"
        assert plan.download_args["local_dir"] == plan.model_dir

    def test_start_cmd_pins_localhost_and_device(self, tmp_path) -> None:
        plan = ei.build_install_plan("bge-m3", str(tmp_path), device="cpu")
        assert "127.0.0.1" in plan.start_cmd       # AC15 不暴露 0.0.0.0
        assert "--device" in plan.start_cmd
        assert "cpu" in plan.start_cmd

    def test_plan_has_no_download_execution(self, tmp_path) -> None:
        # 计划只给参数，不含实际执行入口（AC27 安装归属在壳层）。
        plan = ei.build_install_plan("bge-m3", str(tmp_path))
        assert isinstance(plan.download_args, dict)  # 仅参数
        assert plan.device in ei.VALID_DEVICES

    def test_default_device_cpu_when_unspecified(self, tmp_path) -> None:
        plan = ei.build_install_plan("bge-m3", str(tmp_path))
        assert plan.device == "cpu"

    def test_mirror_propagated(self, tmp_path) -> None:
        plan = ei.build_install_plan("bge-m3", str(tmp_path), mirror="https://hf-mirror.com")
        assert plan.download_args["endpoint"] == "https://hf-mirror.com"

    def test_pip_install_extras_and_hf_hub_pin(self, tmp_path) -> None:
        """锁住 pip 装 [server,torch] + huggingface_hub<1.0 pin + 升级 pip 步骤。

        踩坑全记录（按时间顺序）：
        - [all]：拉 vision/ct2/audio/tensorrt/onnxruntime-gpu，pip resolver
          backtrack 几十分钟卡死（1.3.5 实测）
        - 只 [server]：torch 不在主依赖（pip show 显示 Requires: numpy,
          huggingface_hub），起 infinity 时 ImportError: torch.nn not available
        - [server,torch] 起来后 BetterTransformerManager NameError：infinity
          acceleration.py:46 引用未定义符号（infinity 自己代码 bug）
        - 试装 [optimum]：pip 21 老 resolver backtrack 45 分钟没装出来
        - 改用 env INFINITY_BETTERTRANSFORMER=false 关掉 BetterTransformer 探测，
          acceleration.py:36 第一行直接 return False 不走 optimum 代码
        - huggingface_hub<1.0：infinity 代码 `from huggingface_hub import
          HfFolder`，hf_hub 1.0 移除该 API
        """
        plan = ei.build_install_plan("bge-m3", str(tmp_path))
        cmd_str = " ".join(plan.pip_install_cmd)
        assert "infinity-emb[server,torch]" in cmd_str, (
            f"必须含 [server,torch] 双 extras（漏了 infinity 起不来）；当前 cmd={cmd_str}"
        )
        assert "[server,torch,optimum]" not in cmd_str and "[optimum]" not in cmd_str, (
            f"不要装 optimum（pip 21 backtrack 45min + 版本地狱）；当前 cmd={cmd_str}"
        )
        assert "huggingface_hub<1.0" in cmd_str, (
            f"必须 pin huggingface_hub<1.0 避开 HfFolder ImportError；当前 cmd={cmd_str}"
        )
        assert "pip install --upgrade pip" in cmd_str, (
            f"必须先升级 pip（venv 默认 pip 21.2.4 resolver 太旧）；当前 cmd={cmd_str}"
        )

    def test_env_disables_bettertransformer(self, tmp_path) -> None:
        """plan.env 必须含 INFINITY_BETTERTRANSFORMER=false。

        否则 infinity acceleration.py 模块顶层 from optimum.bettertransformer
        import 或者 check_if_bettertransformer_possible() 内部引用未定义的
        BetterTransformerManager → 进程崩。Swift StartHandler 启动时把 plan.env
        merge 进 Process.environment。
        """
        plan = ei.build_install_plan("bge-m3", str(tmp_path))
        assert plan.env.get("INFINITY_BETTERTRANSFORMER") == "false", (
            f"plan.env 必须有 INFINITY_BETTERTRANSFORMER=false；当前={plan.env}"
        )

    def test_start_cmd_includes_explicit_port(self, tmp_path) -> None:
        """start_cmd 必须显式传 --port，不然 infinity v2 用自己默认 7997。

        Swift StartHandler 按 plan.port 探活 /health，端口对不上 warmup
        必 timeout。Phase 2 设计漏了 --port，1.3.0~1.3.6 dmg 装机后 install
        阶段过了，start 阶段直接卡 warmup 不报错（Phase 3b checkpoint 标的
        follow-up）。
        """
        plan = ei.build_install_plan("bge-m3", str(tmp_path))
        assert "--port" in plan.start_cmd, f"start_cmd 缺 --port；当前={plan.start_cmd}"
        port_idx = plan.start_cmd.index("--port")
        port_val = int(plan.start_cmd[port_idx + 1])
        assert port_val == plan.port, (
            f"--port 参数 ({port_val}) 必须等于 plan.port ({plan.port})"
        )
