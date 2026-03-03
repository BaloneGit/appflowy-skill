# Snapshot / Rollback 协议（v0.3 M4）

## 目标
- 为高风险写操作提供可追溯的恢复点。
- 在执行迁移前先做快照，执行异常后可按策略回退。

## 快照对象
- 支持对象：
  - `database`（`collab_type=1`）
  - `doc`（`collab_type=0`）
- 快照命令：
  - `snapshot-collab`
- 回滚命令：
  - `rollback-collab`

## Snapshot 文件结构（v1）
```json
{
  "schema_version": "v1",
  "snapshot_kind": "collab",
  "created_at": "2026-03-03T00:00:00Z",
  "workspace_id": "<workspace_id>",
  "object_id": "<object_id>",
  "collab_type": 1,
  "collab_kind": "database",
  "doc_state": [1, 2, 3],
  "state_vector": [4, 5, 6],
  "checksums": {
    "doc_state_sha256": "<sha256>",
    "state_vector_sha256": "<sha256>"
  },
  "stats": {
    "doc_state_len": 1234,
    "state_vector_len": 56
  },
  "database_schema_fields": [],
  "collab_json": {}
}
```

字段说明：
- `doc_state/state_vector`：collab 原始字节数组。
- `checksums`：完整性校验。
- `database_schema_fields`：仅 database 快照包含，用于 schema 级回滚。
- `collab_json`：可选，便于人工排查。

## 回滚策略
- `state-update`：
  - 用 snapshot 的 `doc_state` 直接回放到 collab。
  - 适用于 doc 对象或简单恢复场景。
- `schema-database`：
  - 仅 database 使用。
  - 以 `database_schema_fields` 为目标，对当前 schema 执行补齐/改名/删除/select 修复。
  - 默认 `auto` 会对 database 选择该策略。

## 运行约束
- 默认 dry-run，执行必须 `--execute --yes`。
- 快照与回滚目标默认必须匹配；若强制覆盖需 `--allow-target-mismatch`。
- 所有执行都写入 `audit_log`（默认 `.tmp/audit_logs/`）。

## 推荐流程
1. 执行前快照：
```bash
python skills/appflowy-api/scripts/appflowy_skill.py snapshot-collab \
  --config skills/appflowy-api/references/config.example.json \
  --email <email> --password <password> \
  --workspace-id <workspace_id> --database-id <database_id>
```
2. 执行迁移/修复：
```bash
python skills/appflowy-api/scripts/appflowy_skill.py apply-schema-migration ...
```
3. 异常时 dry-run 回滚预览：
```bash
python skills/appflowy-api/scripts/appflowy_skill.py rollback-collab \
  --config skills/appflowy-api/references/config.example.json \
  --email <email> --password <password> \
  --snapshot-file <snapshot.json>
```
4. 确认后执行回滚：
```bash
python skills/appflowy-api/scripts/appflowy_skill.py rollback-collab \
  --config skills/appflowy-api/references/config.example.json \
  --email <email> --password <password> \
  --snapshot-file <snapshot.json> --execute --yes
```

## 已知限制
- `state-update` 不保证对所有 CRDT 冲突都能“硬覆盖”。
- database 回滚优先使用 `schema-database`，可靠性更高。
- 当前版本不提供自动“多版本回滚链管理”（M4 后续可扩展）。
