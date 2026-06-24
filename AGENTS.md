# knowledge-base-system 规则说明

## 1. 适用范围

本文件是仓库根规则，作用于整个 `knowledge-base-system` 项目。

## 2. 产品契约（必须遵守）

1. 两种后端代码路径并存：
   - 直装版（主路线，已交付安装包）：使用 SQLite。
   - PostgreSQL 后端（Docker 版，规划中）：代码已实现，Docker 编排 artefact 在路线图上（v1.x 计划）。
2. 后端选择必须显式、由 `KB_BACKEND` 驱动，不依赖隐式默认值。

## 3. 跨平台一致性规则（重要）

1. macOS 与 Windows 的核心行为必须一致：
   - API 行为一致
   - 数据模型与检索逻辑一致
   - MCP 工具语义一致
2. 平台差异仅允许出现在“壳层”：
   - `windows-app/` 与 Windows 打包/启动脚本
   - `mac-app/` 与 macOS 打包/启动脚本
   - 模式相关启动脚本
3. 未经明确批准，不得按平台分叉核心业务逻辑。

## 4. 分层边界

1. 核心服务与业务逻辑统一放在共享模块（例如 `app/`）。
2. OS 特定进程管理、UI 集成、安装打包放在平台目录（`windows-app/`、`mac-app/`、脚本层）。
3. 不要把平台专属实现混入核心模块。

## 5. 文档策略

1. 面向用户文档与内部工程文档必须明确分区。
2. `docs/README.md` 作为文档路由入口，必须保持更新。
3. 行为变更时需要同步更新：
   - 用户使用文档
   - 内部工程/运维文档

## 6. 配置来源与优先级

1. 用户侧不要求手工维护 `.env` 文件。
2. 运行配置主路径：
   - 业务与模型配置：写入数据库 `system_config` 表（`/settings` 管理），作为唯一可信源。
   - 直装版启动引导：`config/config.toml`（host / port / sqlite_path / qdrant_local_path）。
   - 安装目录定位：`KB_APP_ROOT` 环境变量（由 `kb-start.sh` export），PyInstaller frozen binary 用它定位 `VERSION` / `config/` / `scripts/`。
3. 优先级规则：
   - 后端与连接入口（`KB_BACKEND`、`DATABASE_URL`、`SQLITE_PATH`、`QDRANT_*`）以启动入口注入的 env 为准。
   - LLM / Embedding / Rerank 等模型参数仅以数据库 `system_config` 为准；不再支持环境变量配置。

## 7. 端口与重启契约

1. 端口单一事实源：
   - 直装版：`config/config.toml` 的 `[server].port`（默认 18000）。
   - PostgreSQL 后端 / Docker 编排：端口由调用方编排管理。
2. `/settings` 写入 `service_port` 时：
   - 直装版必须同步回写 `config/config.toml`。
   - PostgreSQL 后端仅更新数据库并返回“端口由编排管理”的提示，不伪装已生效。
3. `/v1/system/restart` 必须按平台与模式分发：
   - Windows 直装：`scripts/local-restart-direct.ps1`（fallback `local-restart.ps1`）
   - macOS 直装：`mac-app/restart.sh`
   - PostgreSQL 后端：返回 HTTP 409 + “请通过编排层重启”提示
   - 不支持的平台返回 HTTP 501
4. 禁止在核心模块硬编码单平台重启命令。

## 8. Agent 接入契约

1. 面向用户支持两条接入路径：`MCP（HTTP 代理）` 与 `Skill（行为规则）`；可单独安装，也可同时安装。
2. MCP 路径固定为 `agent-integration/kb-mcp-proxy.py`（HTTP 代理），不得要求用户直连源码 `python -m app.mcp_server`。
3. Skill 规则源文件固定在 `agent-integration/SKILL.md`（以及 `skills/claude/knowledge-base-first/SKILL.md` 同步副本）。
4. 源码直连仅允许作为“开发者调试路径”标注，且不能作为默认用户路径。
5. 安装入口必须分离：`MCP` 与 `Skill` 分别提供独立安装脚本（Claude/Codex 各自独立）。
