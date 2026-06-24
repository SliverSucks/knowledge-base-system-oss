# Retrieval API / 接口文档

交互式文档（自动生成，始终最新）：`http://127.0.0.1:18000/docs`

---

## 1. 健康检查

`GET /health`

Response:
```json
{"status": "ok"}
```

---

## 1.1 读取产品版本号

`GET /v1/system/version`

返回当前运行实例的产品版本号。无参数。

Response:
```json
{"version": "1.2.2"}
```

版本号来源（按优先级）：
1. `$KB_APP_ROOT/VERSION` 文件（由 `kb-start.sh` export `KB_APP_ROOT` 指向安装目录，例 `/Applications/KnowledgeBase/VERSION` 或 `%LocalAppData%\KnowledgeBase\VERSION`）
2. 项目根目录的 `VERSION` 文件（开发环境）
3. 环境变量 `KB_APP_VERSION`
4. 默认值 `"dev"`

控制台左下角的版本徽章读取此接口。

---

## 2. 检索知识

`POST /v1/knowledge/search`

Request:
```json
{
  "query": "为什么我们在项目A选择JWT短期Token？",
  "domain": "work",
  "project": "project-a",
  "module": "auth",
  "feature": "jwt-login",
  "tags": ["security", "token"],
  "source_uri": "spec://auth",
  "as_of": "2026-04-27T00:00:00Z",
  "top_k": 8,
  "actor": "codex-local"
}
```

必填：`query`、`domain`。其余可选。

Response（命中）:
```json
{
  "results": [
    {
      "knowledge_item_id": "...",
      "version": 3,
      "score": 0.91,
      "snippet": "...",
      "title": "Auth token strategy",
      "source": [{"type": "source_uri", "uri": "spec://auth/v2"}]
    }
  ],
  "trace_id": "...",
  "knowledge_item_ids": ["..."]
}
```

Response（未命中）:
```json
{"results": []}
```

说明：
- `trace_id` / `knowledge_item_ids` 仅命中时返回。
- `module/feature/tags/source_uri/as_of` 为可选过滤字段，防止跨域混入。
- `as_of` 支持时间切片检索（effective_from/effective_to 窗口）。
- `actor` 用于 ACL 过滤，默认 `manual`。

---

## 3. 按 ID 获取知识条目

`GET /v1/knowledge/items/{item_id}`

Query param：`actor`（可选，默认 `manual`）。

Response:
```json
{
  "knowledge_item_id": "...",
  "title": "Auth token strategy",
  "domain": "work",
  "project": "project-a",
  "module": "auth",
  "feature": "jwt-login",
  "tags": ["security"],
  "source_uri": "spec://auth",
  "effective_from": null,
  "effective_to": null,
  "type": "decision",
  "status": "active",
  "version": 3,
  "content_markdown": "...",
  "summary": "...",
  "updated_at": "2026-04-27T12:00:00Z",
  "sources": []
}
```

未找到或 ACL 拒绝返回 `404`。

---

## 4. 写入 / 更新知识

`POST /v1/knowledge/items/upsert`

Request:
```json
{
  "title": "Auth token strategy",
  "domain": "work",
  "project": "project-a",
  "type": "decision",
  "content_markdown": "Use short-lived access token + refresh token.",
  "summary": "Token strategy summary",
  "author": "alice",
  "change_note": "initial",
  "knowledge_item_id": null,
  "module": "auth",
  "feature": "jwt-login",
  "tags": ["security", "token"],
  "source_uri": "spec://auth/v2",
  "effective_from": null,
  "effective_to": null,
  "public_read": true,
  "acl_actors": []
}
```

必填：`title`、`domain`、`type`、`content_markdown`、`author`。其余可选。

- `knowledge_item_id` 为空时新建，不为空时追加新版本。
- `public_read=false` + `acl_actors` 配合使用实现 ACL 私有化。
- **已被软删除的条目（`status='deleted'`）不可通过 upsert 复活**：传入对应 `knowledge_item_id` 会返回 `409 Conflict`，需手动改库恢复。

Response:
```json
{"knowledge_item_id": "...", "version": 1}
```

错误码：
- `409`：目标条目已被软删除，拒绝复活。

---

## 4.1 单文件导入（multipart 上传）

`POST /v1/knowledge/import-file`

直接上传一个文档文件（`.md` / `.markdown` / `.txt` / `.docx` / `.pdf`），服务端 in-process 解析正文 → 推断 title / summary / tags → 调 upsert 入库。配合 menubar 桌面 App「导入知识包」菜单使用，调用方完全不需要本地装 Python 或 OCR 工具链。

Request (`multipart/form-data`):

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | binary | ✅ | 上传的文档文件，按文件名后缀分发解析器 |
| `project` | string | ✅ | 所属项目 |
| `domain` | string | ✅ | `work` / `personal` |
| `knowledge_type` | string | ❌ | `fact` / `decision` / `runbook` / `lesson`，默认 `fact` |
| `actor` | string | ❌ | 操作者标识，默认 `manual` |
| `title` | string | ❌ | 显式标题；不传则取文档第一行 `# H1`，缺则取文件名（去后缀） |
| `summary` | string | ❌ | 显式摘要；不传则取文档首段（截断 120 字符） |

Response：
```json
{"knowledge_item_id": "...", "version": 1}
```

错误码：
- `400`：解析后正文为空（扫描件 PDF 无文字层 / 空文档）；或字段校验失败。
- `409`：目标条目已被软删除，拒绝复活（带 `knowledge_item_id` 复用时可能触发）。
- `415`：文件后缀不在白名单（例如图片格式）。本期不带 OCR，图片类型暂不支持。
- `422`：必填字段缺失。

curl 示例：
```bash
curl -X POST http://127.0.0.1:18000/v1/knowledge/import-file \
  -F "file=@/path/to/doc.md" \
  -F "project=my-proj" \
  -F "domain=work" \
  -F "knowledge_type=fact"
```

---

## 4.5 删除知识条目（Console 内部接口）

`DELETE /v1/console/knowledge/items/{item_id}?actor=<actor>`

- 仅供本地控制台调用，**`include_in_schema=False`**，不在 OpenAPI 中暴露。
- **软删除语义**：将 `knowledge_item.status` 置为 `deleted` 并清理对应的 Qdrant 向量；`knowledge_version` / `knowledge_chunk` 行保留。
- 删除后，该条目在所有检索（`search` / `ask`）和 `get_item` 接口中都不再可见。
- 删除是**不可逆**的——再次以同 ID upsert 会被拒绝（见 4.复活拒绝）。如需恢复，手动修改数据库 `status='active'`。

Query 参数：
- `actor`（必填，非空）：操作者标识，写入服务端审计日志，便于事后追溯。

Response（200）:
```json
{"ok": true, "knowledge_item_id": "...", "deleted": true}
```

错误码：
- `404`：条目不存在或已被删除。
- `422`：缺少 `actor` 参数。

---

## 5. 智能问答

`POST /v1/knowledge/ask`

Request:
```json
{
  "question": "JWT 为什么要用短期 Access Token？",
  "domain": "work",
  "project": "project-a",
  "top_k_chunks": 5,
  "actor": "manual"
}
```

必填：`question`、`domain`。`top_k_chunks` 范围 1~20，默认 5。

Response（LLM 未配置时）:
```json
{
  "question": "...",
  "answer": null,
  "llm_available": false,
  "llm_error": null,
  "chunks_used": [
    {
      "knowledge_item_id": "...",
      "title": "Auth token strategy",
      "snippet": "...",
      "version": 1
    }
  ]
}
```

LLM 已配置时，`answer` 为字符串，`llm_available` 为 `true`。

---

## 6. 系统配置读取

`GET /v1/system/config`

返回当前所有系统配置（来自数据库）。无参数。

Response（结构与 PUT 一致，并附带两个状态字段）：
```json
{
  "api_base_url": "http://127.0.0.1:18000",
  "service_port": 18000,
  "grafana_url": "http://127.0.0.1:3000",
  "ui_theme": "neo",
  "llm_enabled": false,
  "embedding_enabled": false,
  "rerank_enabled": false,
  "enrichment_enabled": false,
  "restart_required": false,
  "runtime_port_managed_by": null,
  "updated_at": "2026-05-17T10:00:00Z"
}
```

字段说明：
- `restart_required`：PUT 修改 `service_port` 后是否需要重启生效。
- `runtime_port_managed_by`：`null`（直装版，端口由 `config/config.toml` 管理）或 `"docker"`（Docker 版，端口由编排管理）。
- 其余字段含义与 PUT 段相同。

---

## 7. 系统配置更新

`PUT /v1/system/config`

所有配置写入数据库，重启后保留，无需 `.env` 文件。

Request（必填字段）:
```json
{
  "api_base_url": "http://127.0.0.1:18000",
  "grafana_url": "http://127.0.0.1:3000",
  "service_port": 18000,
  "ui_theme": "neo"
}
```

可选字段（LLM / Embedding / Rerank，默认均禁用）:
```json
{
  "llm_enabled": false,
  "llm_api_key": "",
  "llm_base_url": "https://api.openai.com/v1",
  "llm_model": "gpt-4o-mini",
  "llm_timeout_sec": 30.0,
  "llm_temperature": 0.2,
  "llm_max_tokens": 1024,
  "embedding_enabled": false,
  "embedding_api_key": "",
  "embedding_base_url": "",
  "embedding_model": "",
  "embedding_dim": 384,
  "embedding_timeout_sec": 20.0,
  "rerank_enabled": false,
  "rerank_api_key": "",
  "rerank_base_url": "",
  "rerank_model": "",
  "rerank_path": "/rerank",
  "rerank_timeout_sec": 20.0,
  "enrichment_enabled": false
}
```

`ui_theme` 可选值：`linear`、`glass`、`neo`。

Response：返回保存后的完整配置（含 `updated_at`）。

---

## 8. 重启本地服务

`POST /v1/system/restart`

重启链路按模式/平台分发：
- 直装版 Windows（`KB_BACKEND=sqlite` + win32）：触发 `scripts/local-restart-direct.ps1`，taskkill `kb-api.exe` 后重新启动 `bin\kb-api.exe`
- 直装版 macOS（`KB_BACKEND=sqlite` + darwin）：触发 `mac-app/restart.sh`
- Docker 版（`KB_BACKEND=postgres`）：返回 HTTP `409`，body 为 `{"detail":"请通过 docker compose restart 操作"}`
- 其他平台：返回 HTTP `501`
- 脚本缺失：返回 HTTP `404`

Response（成功）:
```json
{"ok": true}
```

---

## 9. 备份恢复（直装版）

### 9.1 导出全量备份

`POST /v1/system/backup/export`

无请求体。响应 `Content-Type: application/gzip` 流式返回 `.tar.gz`。

包内结构：

```
manifest.json
data/knowledge.db
data/qdrant_local/
meta/system_config_redacted.json
```

`manifest.json` 字段：

- `schema_version`：当前固定为 `1`
- `created_at`：UTC ISO8601
- `backend`：`sqlite`
- `host`：备份产生时所在主机名
- `knowledge_db_sha256`：64 hex 字符，对应解压后 `data/knowledge.db` 的 sha256
- `embedding`：`{model, dim, base_url}`（merge 模式只比对 `model+dim` 二元组）
- `stats`：`{items, versions, chunks, vectors}`

响应码：
- `200` + 二进制流：成功
- `501`：非 sqlite backend（postgres 走独立的 `backup-restore-docker-mode` 提案）
- `503`：服务处于 maintenance（已有 backup/restore 在跑）
- `507`：磁盘空间不足。`detail` 含 `required_bytes` / `available_bytes` / `target`

### 9.2 导入全量备份

`POST /v1/system/backup/import`

Console-only，不写入 OpenAPI。

`multipart/form-data` body：
- `file`：备份包 `.tar.gz`
- `mode`：`overwrite` 或 `merge`
- `confirm`：必须严格等于 `I-CONFIRM-OVERWRITE`（overwrite）或 `I-CONFIRM-MERGE`（merge）。任何弱值（`true` / `yes` / `1` / 小写）都会被拒。

行为：
- **overwrite**：清空当前业务表 + Qdrant collection → 整包还原 + 覆盖 system_config（包内真凭证）
- **merge**：按 `knowledge_item_id` 比对，本地不存在则新增；本地已存在非 deleted 则跳过；本地 `status=deleted` 视为可恢复（status→active，version 沿用本地）。

双层防护：
- **外层** auto-backup（永久保留）：操作前 cp 整个 `data/` 到 `~/Library/Application Support/KnowledgeBase/auto-backup/{ts}/`
- **内层** `.pre-restore`（仅 overwrite，过程中临时存在）：cp `knowledge.db` → `.pre-restore.bak`，cp -R `qdrant_local` → `.pre-restore-qdrant/`。成功后删，失败时秒级回滚。

响应码：
- `200` + `{ok, mode, items_after, auto_backup_path, rolled_back}`：成功
- `400`：confirm 错误 / schema_version 不支持 / manifest 字段缺失 / sha256 不匹配 / mode 未知
- `500`：restore 阶段失败但已回滚到 `.pre-restore.*`
- `501`：非 sqlite backend
- `503`：maintenance 期间（已有 import 在跑）

### 9.3 列出历史快照

`GET /v1/system/backup/auto-backups`

倒序返回 `auto-backup/` 下所有快照目录的元数据（时间戳 / 触发源 / 大小 / 包内 stats），便于控制台展示与下载。

---

## MCP 工具映射 / Agent Tool Mapping

| MCP 工具 | 对应接口 |
|----------|----------|
| `search_knowledge` | `POST /v1/knowledge/search` |
| `get_knowledge_item` | `GET /v1/knowledge/items/{id}` |
| `upsert_knowledge` | `POST /v1/knowledge/items/upsert` |
| `import_incremental_knowledge` | `POST /v1/knowledge/import-incremental` |
| `export_knowledge_package` | `POST /v1/knowledge/export-package` |
| `import_knowledge_package` | `POST /v1/knowledge/import-package`（危险，需确认） |
| `clear_knowledge_base` | `POST /v1/knowledge/clear`（危险，需确认） |
| `cleanup_expired_knowledge` | `POST /v1/knowledge/cleanup-expired`（危险，需确认） |
