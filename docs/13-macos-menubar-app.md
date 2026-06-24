# macOS 直装版 / macOS Direct Install

## 1. 适用范围

本文档面向终端用户，说明 macOS 直装版（菜单栏 App）的安装、启停、导入与排障。

## 2. 安装

1. 打开 `KnowledgeBase-mac-direct-<version>.dmg`
2. 双击 `Install.command`
3. 安装目录固定为：`/Applications/KnowledgeBase`
4. 安装完成后会自动打开：`/Applications/KnowledgeBase/KnowledgeBaseMenuBar.app`

## 3. 菜单结构与启停

菜单栏图标点击后展开，每项带 SF Symbol 图标，深色 / 浅色主题自动适配：

| 菜单项 | 行为 |
|---|---|
| 打开知识库工作台 | 浏览器打开 `http://127.0.0.1:{port}/console` |
| 打开 API 文档 | 浏览器打开 `http://127.0.0.1:{port}/docs` |
| 启动知识库 | 触发 `scripts/kb-start.sh` |
| 停止知识库 | 触发 `scripts/kb-stop.sh` |
| 查看状态 | 弹窗显示 health + 版本 + 端口 + PID |
| 知识库管理 ▸ 导入知识包 | 触发 `scripts/kb-import-package.sh`：`.tar.gz` 走备份恢复，`.md/.txt/.docx/.pdf` 走 `POST /v1/knowledge/import-file`（curl + 后端 in-process 解析，无外部 Python 依赖） |
| 知识库管理 ▸ 导出知识包 | 触发 `scripts/kb-export-package.sh` |
| 知识库管理 ▸ 增量导入 | 触发 `scripts/kb-import-incremental.sh` |
| 知识库管理 ▸ 清空知识库 | 触发 `scripts/kb-clear.sh`（需确认） |
| 知识库管理 ▸ 清理过期知识 | 触发 `scripts/kb-clean-expired.sh` |
| 知识库管理 ▸ 重建向量索引 | NSAlert 二次确认 → POST `/v1/system/rebuild-vector-index` + `confirm=I-CONFIRM-OVERWRITE`，跑完弹完成通知（v1.3 新增） |
| 退出 (⌘Q) | 异步停 kb-api 后退出 App |

退出 App（菜单「退出」或 `⌘Q`）会**自动停止后端服务**，无需另外手动跑 `kb-stop.sh`。停服务过程异步执行，2 秒内未完成时 App 仍会先退（孤儿脚本继续跑完），避免被系统强杀。

App 每 4 秒做一次 `/health` 检查，刷新菜单栏状态徽章。

端口来源：
- 优先读取 `/Applications/KnowledgeBase/config/config.toml` 的 `[server].port`
- 未配置时默认 `18000`

## 4. 控制台与健康检查

- 工作台：`http://127.0.0.1:18000/console`（端口按配置变化）
- 健康检查：`http://127.0.0.1:18000/health`
- 当前产品版本：左下角"API 状态"框下方显示 `v<x.y.z>`（也可通过 `GET /v1/system/version` 获取）

也可在终端执行：

```bash
/Applications/KnowledgeBase/scripts/kb-status.sh
```

## 5. 导入知识

菜单栏支持两类导入：
1. 增量导入目录
2. 导入知识包（zip）

导入脚本：
- `/Applications/KnowledgeBase/scripts/kb-import-incremental.sh`
- `/Applications/KnowledgeBase/scripts/kb-import-package.sh`

## 6. 日志与排障

服务日志路径：
- `/Applications/KnowledgeBase/logs/api.log`
- `/Applications/KnowledgeBase/logs/api.err.log`

常见检查项：
1. 端口是否被占用——`kb-start.sh` 启动前会**自动清理上一次残留的 `kb-api` 进程**（PID file 与端口占用双兜底），端口冲突一般无需手动处理
2. `config.toml` 的端口是否与访问地址一致
3. `kb-status.sh` 是否显示 `running`

## 7. 卸载

1. 停止服务：`/Applications/KnowledgeBase/scripts/kb-stop.sh`
2. 删除安装目录：`/Applications/KnowledgeBase`
3. 如需清理用户配置，再删除：
   - `~/Library/Application Support/KnowledgeBase`

## 8. 升级时的数据保留（v1.3 新增）

升级 dmg（双击 `Install.command`）会自动备份并注入以下三个目录到新版本，**用户无需手动干预**：

| 目录 | 保留行为 | 说明 |
|---|---|---|
| `data/` | 必须保留（备份失败立即 abort，不动旧安装） | 知识库 SQLite + Qdrant 数据，核心 |
| `models/` | 尽力保留（失败仅警告） | 1-4 GB 模型权重，失败后新版本走 `/setup` 重下 |
| `embedding-service/` | 尽力保留（失败仅警告） | venv 含 infinity-emb 等依赖，失败后新版本走 `/setup` 重 pip 装 |

实现：APFS clonefile（同卷瞬时，几乎零空间开销）；跨卷退化为真复制。

## 9. 重建向量索引（v1.3 新增）

切 embedding 模型 / 维度后必须重建索引才能让语义检索生效。两个入口任选其一：

**入口 A（推荐）**：菜单栏 → 知识库管理 → 重建向量索引
- 弹原生 NSAlert 二次确认
- 点"开始重建" → 收到第一条通知"向量索引重建已启动"
- 约 1-3 分钟（取决于 chunk 数与模型，~1000 chunk · CPU bge-m3 约 30-60 秒）
- 跑完收到第二条通知"向量索引重建完成 · 处理 X/Y · 用时 N 秒"
- 失败时收到通知"向量索引重建失败 · {错误详情}"

**入口 B**：浏览器打开 `/settings` → 重建按钮（带进度条）

期间不能写入知识库（自动维护态）。重建跑完语义检索立即生效。

## 10. 切换 embedding 模式（v1.3 行为）

`/settings` 改 `embedding_service_mode`（本地 / 外部 / 关闭）+ `embedding_service_model_id` 时，后端会自动触发壳层动作，**用户无需手动启停 infinity**：

| 旧 → 新 | 自动行为 |
|---|---|
| disabled/external → local | 触发 install（壳层接到指令后走 venv 检测 → pip → 模型下载 → start，已装好的部分自动跳过） |
| local → local（改模型） | 触发 switch_model（壳层串联 stop → install → start） |
| local → external/disabled | 触发 stop（壳层 SIGTERM/SIGKILL infinity，释放 ~1.5GB 内存） |
| external ↔ disabled | 不动 infinity（本来就没跑） |

注意：mode 或 model_id 变更**必须带** `confirm_reindex: "I-CONFIRM-REINDEX"` token（防误触发，前端 /setup 自动带）。切换完成后建议立刻走"重建向量索引"刷新向量空间。
