# AppFlowy API 参考（自托管）

## 认证与请求头
- 获取 token（password grant）  
  `POST /gotrue/token?grant_type=password`  
  Body: `{"email":"...","password":"..."}`
- AppFlowy API 必需请求头  
  `Authorization: Bearer <access_token>`  
  `client-version: <部署版本>`  
  `client-timestamp: <Unix 毫秒>`  
  `device-id: <UUID>`

## 常用端点
### Workspace
- `GET /api/workspace`

### 文档 / 视图
- `POST /api/workspace/{workspace_id}/page-view`
- `POST /api/workspace/{workspace_id}/page-view/{view_id}/append-block`
- `POST /api/workspace/{workspace_id}/page-view/{view_id}/database-view`
- `GET /api/workspace/{workspace_id}/folder?depth=...&root_view_id=...`

### 数据库
- `GET /api/workspace/{workspace_id}/database`
- `POST /api/workspace/{workspace_id}/database/{database_id}/fields`
- `POST /api/workspace/{workspace_id}/database/{database_id}/row`
- `PUT /api/workspace/{workspace_id}/database/{database_id}/row`
- `GET /api/workspace/{workspace_id}/database/{database_id}/row/detail?ids=...`
- 字段改名/字段删除：当前无稳定 REST 路由，建议通过 skill 的 collab 命令完成
- `DELETE /api/workspace/{workspace_id}/database/{database_id}/row`：**不支持（通常返回 405）**

### 搜索
- `GET /api/search/{workspace_id}?query=...`

### 协作（Collab）
- `GET /api/workspace/v1/{workspace_id}/collab/{object_id}?collab_type=0`
- `GET /api/workspace/v1/{workspace_id}/collab/{object_id}/json?collab_type=0`
- `POST /api/workspace/v1/{workspace_id}/collab/{object_id}/web-update`

## 请求示例
### 创建页面
```json
{
  "name": "示例文档",
  "view_type": 0,
  "parent_view_id": "<parent_view_id>",
  "parent_view_type": 0
}
```

### 追加块（Quill Delta）
```json
{
  "type": "paragraph",
  "data": {
    "delta": [
      { "insert": "Hello AppFlowy\n" }
    ]
  }
}
```

### 添加字段
```json
{
  "name": "标题",
  "field_type": 0
}
```

### 写入/更新行（cells）
```json
{
  "pre_hash": "example:row-key",
  "cells": {
    "Name": "示例",
    "状态": "未开始",
    "标签": ["核心", "风险"]
  }
}
```

### Select 字段写入约定（重要）
- `SingleSelect`/`MultiSelect` 用**选项名称**写入（字符串/字符串数组）。
- 不要把 `selected_option_ids` 直接传给这两类字段的 row API；该结构常用于 `Checklist` 字段。

## skill 命令补充（v0.2）

### `database-query`
- 脚本：`python skills/appflowy-api/scripts/database_query.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py database-query ...`
- 支持：
  - 查询参数：`filter` / `sort` / `limit` / `offset`
  - 快速参数：`--filter-field/--filter-op/--filter-value`、`--sort-field/--sort-direction`
  - 输出计数：`total_row_ids`、`rows_loaded`、`rows_after_filter`、`rows_returned`
- `--query-file` 支持 UTF-8 与 UTF-8 BOM。

### `page-get-tree`
- 脚本：`python skills/appflowy-api/scripts/page_tree.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py page-get-tree ...`
- 支持 `--depth`、`--root-view-id`、`--compact`。

### `page-get-blocks`
- 脚本：`python skills/appflowy-api/scripts/page_blocks.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py page-get-blocks ...`
- 支持 `--raw`（原始 collab JSON）与 `--flat`（扁平 block 列表）。

### `page-delete-blocks`
- 脚本：`python skills/appflowy-api/scripts/delete_page_blocks.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py page-delete-blocks ...`
- 支持 `--dry-run`、`--block-id/--block-ids/--block-ids-file`。

### `rename-db-field`
- 脚本：`python skills/appflowy-api/scripts/rename_db_field.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py rename-db-field ...`
- 支持按 `--field-id` 或 `--field-name` 定位字段（同名字段会要求改用 id）。

### `delete-db-field`
- 脚本：`python skills/appflowy-api/scripts/delete_db_field.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py delete-db-field ...`
- 默认 dry-run；实际删除必须传 `--execute --yes`。
- 禁止删除主字段（primary field）。

### `bulk-upsert-rows`
- 脚本：`python skills/appflowy-api/scripts/bulk_upsert_rows.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py bulk-upsert-rows ...`
- 输入：JSON 数组，或 `{ "rows": [...] }`，每项需包含 `cells` 和 `pre_hash`/`key`。
- 输出：新增/更新/失败/跳过统计与行级结果。

### 统一变更输出（M3）
- 删除类和批量写入类命令会返回 `change_report`。
- `change_report` 结构：
  - `before`：执行前快照（例如 row_count_before）
  - `plan`：计划变更（例如 planned_delete_count / planned rows）
  - `after`：执行后快照（例如 row_count_after / row_count_diff）
  - `summary`：统计摘要（例如 added_count / updated_count / failed_count）

## skill 命令补充（v0.3 M1）

### `schema-diff`
- 脚本：`python skills/appflowy-api/scripts/schema_diff.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py schema-diff ...`
- 输入：当前库 + 目标模板（`--target-template-file`）或目标库（`--target-database-id`）。
- 输出分段：`add_fields` / `delete_fields` / `rename_candidates` / `type_changes` / `select_option_changes`。
- 默认过滤系统字段（`CreatedTime`/`LastEditedTime`/`CreatedBy`/`LastEditedBy`），可用 `--include-system-fields` 打开。
- `rename_candidates` 是建议项，不直接作为执行依据；后续执行应优先使用 `field_id` 再确认。

### `schema-migration-plan`
- 脚本：`python skills/appflowy-api/scripts/schema_migration_plan.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py schema-migration-plan ...`
- 基于 schema diff 输出 `operations`、`op_counts`、`risk_counts`、`blocked_operations`。
- 风险模型：
  - `add_field`：`low`
  - `rename_field`：按置信度 `medium/high`
  - `delete_field`：`high`
  - `change_field_type`：`high`，默认不可自动执行
- 默认不执行任何写操作，仅用于评审迁移计划。

### v0.3 M1 输出约定
- `schema-diff` 与 `schema-migration-plan` 均输出 `change_report`，结构为 `before/plan/after/summary`。
- 对于主字段删除、类型迁移等高风险操作，plan 会标记 `auto_executable=false` 并进入 `blocked_operations`。

## skill 命令补充（v0.3 M2）

### `apply-schema-migration`
- 脚本：`python skills/appflowy-api/scripts/apply_schema_migration.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py apply-schema-migration ...`
- 输入模式：
  - 直接模式：`--target-database-id` 或 `--target-template-file`（现场生成 diff+plan）。
  - 计划模式：`--plan-file`（读取已审阅 plan 执行）。
- 默认 `dry-run`。执行必须显式传：`--execute --yes`。

### 执行护栏（guardrails）
- 高风险操作（如 `delete_field`）执行时要求：`--allow-high-risk`。
- 字段删除操作额外要求：`--allow-delete-fields`。
- 主字段删除仍被阻断（blocked）。
- 默认提示“先做快照再执行”，当前版本不自动回滚。

### 执行覆盖范围（v0.3 M2）
- 支持执行：`add_field` / `rename_field` / `delete_field` / `update_select_options`。
- 暂不自动执行：`change_field_type`（保持 manual review）。
- 对 `Relation` 新增字段：若 `database_id` 未解析（例如 `<db_id_placeholder>`），会要求人工处理。

### before/after diff 复核
- `apply-schema-migration` 会输出执行前后的 diff 摘要：
  - `before_diff`：执行前与目标 schema 差异
  - `after_diff`：执行后与目标 schema 差异
- 若 plan 文件未包含 `target_schema`，会提示 `after_diff` 不可用。
- `--plan-file` 支持 UTF-8 / UTF-8 BOM / UTF-16 编码。

## skill 命令补充（v0.3 M3）

### 模板变量协议（template_vars）
- 在模板根节点新增 `template_vars`：
  - key：变量名
  - value：变量声明（支持 `required`/`default`/`type`/`description`）
- 模板中以 `{{var_name}}` 使用变量。
- 若变量占满整段字符串，支持渲染为非字符串类型（number/array/object）。
- `apply-grid` 已支持 `--vars` / `--vars-file`，可直接消费参数化模板。

### `render-template`
- 脚本：`python skills/appflowy-api/scripts/render_template.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py render-template ...`
- 支持：
  - `--vars` / `--vars-file`
  - 默认值注入与必填校验
  - 类型校验（`string/number/boolean/array/object`）
  - 输出到 `--output-file`
- 每次执行会落地 `audit_log`（默认 `.tmp/audit_logs/`）。

### `repair-runner`
- 脚本：`python skills/appflowy-api/scripts/repair_runner.py ...`
- 统一入口：`python skills/appflowy-api/scripts/appflowy_skill.py repair-runner ...`
- 规则接口（可组合）：
  - `cleanup-default-rows`：清理默认空行
  - `ensure-template-fields`：补齐模板缺失字段（结构修复）
  - `repair-select-options`：修复 select 字段选项
- 默认 `dry-run`；执行需 `--execute --yes`。
- 每次执行会落地 `audit_log`（默认 `.tmp/audit_logs/`）。

### 正确样例
```bash
python skills/appflowy-api/scripts/appflowy_skill.py render-template \
  --template-file skills/appflowy-api/references/templates/grid_plan.with_vars.example.json \
  --vars-file skills/appflowy-api/references/templates/grid_plan.vars.example.json \
  --output-file .tmp/grid_plan.rendered.json
```

```bash
python skills/appflowy-api/scripts/appflowy_skill.py repair-runner \
  --config skills/appflowy-api/references/config.example.json \
  --email <email> --password <password> \
  --workspace-id <workspace_id> --database-id <database_id> \
  --template-file .tmp/grid_plan.rendered.json \
  --repair ensure-template-fields --repair repair-select-options --execute --yes
```

### 失败样例
- 缺少必填变量：
```bash
python skills/appflowy-api/scripts/appflowy_skill.py render-template \
  --template-file skills/appflowy-api/references/templates/grid_plan.with_vars.example.json
```
- 现象：报 `Missing required template vars`。

- 变量类型不匹配（例如 `status_options` 应为 array）：
```bash
python skills/appflowy-api/scripts/appflowy_skill.py render-template \
  --template-file skills/appflowy-api/references/templates/grid_plan.with_vars.example.json \
  --vars '{"status_options":"not-array"}'
```
- 现象：报 `Template var type mismatch`。

- 未传模板却调用模板修复规则：
```bash
python skills/appflowy-api/scripts/appflowy_skill.py repair-runner \
  --config skills/appflowy-api/references/config.example.json \
  --email <email> --password <password> \
  --workspace-id <workspace_id> --database-id <database_id> \
  --repair ensure-template-fields
```
- 现象：报 `require --template or --template-file`。

## 错误处理
- HTTP 200 但响应体包含 `success=false` 或 `error` 视为业务失败。
- 控制台提示无法连接时，优先检查宿主机 `80/443` 端口与防火墙规则。
- 容器间调用优先使用内部地址（如 `http://gotrue:9999`、`http://appflowy_cloud:8000`）。

## 说明
- 模板文件与脚本输出请使用 UTF-8，避免中文乱码。
- Grid 默认可能生成 3 条空行，建议在写入真实数据前清理。
- 删除行建议使用技能脚本 `python skills/appflowy-api/scripts/delete_rows.py ...`（通过 collab 更新 row_orders）。
