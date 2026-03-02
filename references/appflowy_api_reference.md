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

## 错误处理
- HTTP 200 但响应体包含 `success=false` 或 `error` 视为业务失败。
- 控制台提示无法连接时，优先检查宿主机 `80/443` 端口与防火墙规则。
- 容器间调用优先使用内部地址（如 `http://gotrue:9999`、`http://appflowy_cloud:8000`）。

## 说明
- 模板文件与脚本输出请使用 UTF-8，避免中文乱码。
- Grid 默认可能生成 3 条空行，建议在写入真实数据前清理。
- 删除行建议使用技能脚本 `python skills/appflowy-api/scripts/delete_rows.py ...`（通过 collab 更新 row_orders）。
