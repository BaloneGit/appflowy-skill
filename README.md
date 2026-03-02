# AppFlowy API Skill

用于自托管 AppFlowy 的 API 调用与自动化：登录获取 token、文档/视图/数据库操作、搜索、协作数据更新等。

## 入口方式
本技能提供两类入口：
1. **单脚本入口**：直接运行某个脚本，如 `python scripts/doctor.py ...`  
   - 优点：脚本即文档，参数最清晰  
   - 适合：脚本级调试、精细控制
2. **统一入口**：`python scripts/appflowy_skill.py <command> ...`  
   - 优点：命令风格统一、便于上层工具封装  
   - 适合：自动化流程、对外集成

查看统一入口可用命令：
```bash
python skills/appflowy-api/scripts/appflowy_skill.py list
```

查看某个命令帮助：
```bash
python skills/appflowy-api/scripts/appflowy_skill.py help apply-grid
python skills/appflowy-api/scripts/appflowy_skill.py help database-query
python skills/appflowy-api/scripts/appflowy_skill.py help page-get-tree
python skills/appflowy-api/scripts/appflowy_skill.py help page-get-blocks
```

## 结构
- `SKILL.md`：技能说明（供 Codex/Claude/OpenClaw 读取）
- `scripts/`：可复用脚本与通用库
- `references/`：API 参考与模板文件（UTF-8）
- `examples/`：示例命令与用法

## 快速示例
```bash
# 自检
python skills/appflowy-api/scripts/appflowy_skill.py doctor --config skills/appflowy-api/references/config.example.json --email <email> --password <password>

# 应用 Grid 模板（就地修改）
python skills/appflowy-api/scripts/appflowy_skill.py apply-grid --config skills/appflowy-api/references/config.example.json --email <email> --password <password> --workspace-id <workspace_id> --view-id <view_id> --template-file skills/appflowy-api/references/templates/fitness_plan.example.json

# 删除行（通过 collab 更新 row_orders）
python skills/appflowy-api/scripts/appflowy_skill.py delete-rows --config skills/appflowy-api/references/config.example.json --email <email> --password <password> --workspace-id <workspace_id> --database-id <database_id> --row-ids <row_id_1,row_id_2>

# 查询数据库（推荐 query-file）
python skills/appflowy-api/scripts/appflowy_skill.py database-query --config skills/appflowy-api/references/config.example.json --email <email> --password <password> --workspace-id <workspace_id> --database-id <database_id> --query-file .tmp/db_query.json

# 读取页面树
python skills/appflowy-api/scripts/appflowy_skill.py page-get-tree --config skills/appflowy-api/references/config.example.json --email <email> --password <password> --workspace-id <workspace_id> --depth 3 --compact

# 读取页面 block（树形 / 扁平）
python skills/appflowy-api/scripts/appflowy_skill.py page-get-blocks --config skills/appflowy-api/references/config.example.json --email <email> --password <password> --workspace-id <workspace_id> --view-id <view_id>
python skills/appflowy-api/scripts/appflowy_skill.py page-get-blocks --config skills/appflowy-api/references/config.example.json --email <email> --password <password> --workspace-id <workspace_id> --view-id <view_id> --flat
```

> 约定：`SingleSelect`/`MultiSelect` 行值请使用选项名称，不要直接提交 `selected_option_ids`。
>
> 兼容性：`database-query` 读取 `--query-file` 时支持 UTF-8 与 UTF-8 BOM（Windows PowerShell 默认 UTF-8 输出可直接使用）。
