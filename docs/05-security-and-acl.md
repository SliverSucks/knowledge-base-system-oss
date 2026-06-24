# Security And ACL / 安全与权限

## Policy Principles / 策略原则

1. Deny by default.
   默认拒绝。
2. ACL filter happens before retrieval scoring.
   先做 ACL，再做召回打分。
3. Separate `work` and `personal` by policy, not by device.
   工作/个人按策略隔离，而不是按设备隔离。
4. Treat the host as the security boundary; the service only listens on `127.0.0.1`.
   以本机为安全边界，API 仅监听 `127.0.0.1`。

## Network And Request Origin / 网络与请求来源

1. CORS allow-list
   - Allowed: `^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$`，外部域名一律拒绝。
   - 浏览器跨域读响应被 CORS 阻断。

2. Origin Guard middleware（CSRF 深度防御）
   - 适用方法：`POST` / `PUT` / `PATCH` / `DELETE`，`GET/HEAD/OPTIONS` 不拦。
   - 检查 `Origin`，回退检查 `Referer`；两者均无视为 server-to-server / curl，放行。
   - 仅 `http(s)://(127.0.0.1|localhost|[::1])[:port]` 视为合法来源。
   - `Origin: null`、`file://`、外部域名一律 403。
   - 与 CORS 互补：CORS 挡浏览器读响应，本中间件挡 `multipart/form-data` 等 simple request 在到达业务逻辑前。

## Actor And ACL / 调用方与权限过滤

1. `actor` parameter
   - 出现在 `search / get / upsert / ask / delete` 等所有业务接口的请求体或查询参数中。
   - 默认值：`manual`（手动调用、控制台、本机 CLI）。
   - 控制台删除等 mutation 强制要求显式 actor，不可为空。

2. Item-level ACL
   - `public_read = true`：所有 actor 可读。
   - `public_read = false`：仅当 actor 出现在 `acl_actors` 列表内才允许读取与展示。
   - ACL 过滤发生在召回打分之前，未授权条目不会出现在结果中（连 `knowledge_item_id` 都不会暴露）。

3. Domain isolation
   - `work` 与 `personal` 不会混入同一次查询结果，由调用方在 `domain` 参数中指定。

## Operation Safeguards / 危险操作保护

1. Maintenance mode（写类请求服务降级）
   - 触发：进程级 flag 被设置（如导入/恢复进行中），由 `MaintenanceMiddleware` 拦截。
   - 行为：写类请求返回 `503 Service Unavailable` + `Retry-After: 60`。
   - 例外放行：`GET/HEAD/OPTIONS`、`POST /v1/knowledge/search`、`POST /v1/knowledge/ask`、`POST /v1/system/recover/*`（解除入口必须可达）。

2. Confirm token（语义化二次确认）
   - 危险操作必须在请求体携带与 `mode` 匹配的字面 token，错误立即 400 + 提示。
   - 现行 token：
     - `I-CONFIRM-OVERWRITE`：覆盖导入 / 清空知识库
     - `I-CONFIRM-MERGE`：合并导入
     - `I-CONFIRM-ROLLBACK`：回滚 `.pre-restore` 残留
     - `I-CONFIRM-DISCARD`：丢弃 `.pre-restore` 残留
   - 设计目的：防止脚本误调或 prompt-injected agent 直接执行破坏性动作。

3. Data path boundary（落盘路径白名单）
   - 备份导出、增量导入、`backup_dir` 等所有写文件参数都经过 `_ensure_path_allowed` 检查。
   - 默认允许：项目数据目录、用户家目录下的 KnowledgeBase 子目录、`/tmp` 内的临时区域。
   - 扩展方式：`KB_DATA_ROOTS` 环境变量，冒号分隔追加额外根。
   - 越界路径直接拒绝，避免 path traversal 写到任意位置。

4. Archive safety（压缩包导入防护）
   - 解包前校验 tar entry 不含 `..`、绝对路径、符号链接，挡 zip slip。
   - 校验 `manifest.json` 中的 `schema_version` 与 `db_sha256`，不通过则不替换数据。
   - 导入前自动备份当前 data 到 `.pre-restore`，失败可回滚。

## Auditing / 审计

每次检索与读取记录 / Every search/read logs：
- actor（调用者）
- query hash（查询哈希，不存原文）
- domains/projects requested（请求域与项目）
- result IDs returned（返回结果 ID）
- timestamp + trace ID（时间与 `trace_id`）

每次破坏性操作额外记录 / Mutations additionally log：
- 操作类型（delete / clear / import-package / restore 等）
- actor（必填，不可为空）
- 目标条目 `knowledge_item_id` 或包路径
- 操作时间

## Data Retention / 数据保留

1. Keep canonical knowledge versions by default.
   默认保留知识历史版本。
2. Allow source refs to expire independently.
   来源引用可独立过期。
3. Preserve history for traceability.
   保留历史以便追溯。
4. Auto-backup snapshots from installer are kept indefinitely under `~/Library/Application Support/KnowledgeBase/auto-backup/` (macOS) / `%LocalAppData%\KnowledgeBase\auto-backup\` (Windows). 建议定期手动清理。

## Secret Handling / 凭证处理

1. LLM / Embedding / Rerank API keys 以明文存储于 `system_config` 表，仅适用于本机隔离的单用户场景。
2. 备份导出（`/v1/system/backup/export`、`/v1/knowledge/export-package`）会脱敏：API key 字段不写入 tarball。
3. 控制台 UI 显示完整 key（本机访问），分享截图前请自行打码。
4. 切勿将运行中的 `data/knowledge.db` 直接分享给他人，等同分享 key。
