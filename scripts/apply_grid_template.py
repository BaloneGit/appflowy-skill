import argparse
import json

import doc_grid_lib as grid_lib
from _common import build_client, load_json_payload, print_json, resolve_token
from appflowy_client import AppFlowyError


def load_template(path_or_payload: str | None, payload_file: str | None) -> dict:
    if path_or_payload or payload_file:
        return load_json_payload(path_or_payload, payload_file)
    raise AppFlowyError("Missing template payload. Use --template or --template-file.")


def field_type_to_select_key(field_type: object) -> str | None:
    if isinstance(field_type, int):
        if field_type == 3:
            return "3"
        if field_type == 4:
            return "4"
        return None
    if isinstance(field_type, str):
        normalized = field_type.strip()
        if normalized == "SingleSelect":
            return "3"
        if normalized == "MultiSelect":
            return "4"
        if normalized in {"3", "4"}:
            return normalized
    return None


def parse_select_content(value: object) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if isinstance(value, dict):
        if "options" in value:
            return {
                "options": value.get("options") or [],
                "disable_color": bool(value.get("disable_color", False)),
            }
        if "content" in value:
            return parse_select_content(value.get("content"))
    return {}


def normalize_select_type_option_data(type_option_data: object) -> dict | None:
    if not isinstance(type_option_data, dict):
        return None
    parsed = parse_select_content(type_option_data)
    if parsed.get("options"):
        return {"content": json.dumps(parsed, ensure_ascii=False)}
    if isinstance(type_option_data.get("content"), dict):
        return {
            **type_option_data,
            "content": json.dumps(type_option_data["content"], ensure_ascii=False),
        }
    if isinstance(type_option_data.get("options"), list):
        payload = {
            "options": type_option_data.get("options") or [],
            "disable_color": bool(type_option_data.get("disable_color", False)),
        }
        return {"content": json.dumps(payload, ensure_ascii=False)}
    return dict(type_option_data)


def extract_select_payload(type_option_data: object) -> dict:
    if not isinstance(type_option_data, dict):
        return {}
    parsed = parse_select_content(type_option_data)
    if parsed.get("options"):
        return parsed
    return {}


def build_select_field_updates(fields: list[dict]) -> list[dict]:
    select_fields = []
    for field in fields:
        type_key = field_type_to_select_key(field.get("field_type"))
        if not type_key:
            continue
        data = extract_select_payload(field.get("type_option_data"))
        options = data.get("options") or []
        if options:
            select_fields.append(
                {
                    "name": field.get("name"),
                    "options": options,
                    "disable_color": bool(data.get("disable_color", False)),
                }
            )
    return select_fields


def build_select_value_lookup(fields_resp: dict) -> dict:
    fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []
    by_name = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        type_key = field_type_to_select_key(field.get("field_type"))
        if not name or not type_key:
            continue
        type_option = field.get("type_option") or {}
        content = None
        if isinstance(type_option, dict):
            if isinstance(type_option.get("content"), (dict, str)):
                content = type_option.get("content")
            typed = type_option.get(type_key)
            if content is None and isinstance(typed, dict):
                content = typed.get("content")
        parsed = parse_select_content(content)
        options = parsed.get("options") or []
        id_to_name = {}
        for option in options:
            if isinstance(option, dict) and option.get("id") and option.get("name"):
                id_to_name[option["id"]] = option["name"]
        by_name[name] = {"type_key": type_key, "id_to_name": id_to_name}
    return by_name


def normalize_select_value(value: object, type_key: str, id_to_name: dict) -> object:
    is_multi = type_key == "4"
    if isinstance(value, dict):
        selected_ids = value.get("selected_option_ids")
        if isinstance(selected_ids, list):
            names = [id_to_name.get(item) for item in selected_ids if item in id_to_name]
            if is_multi:
                return names
            return names[0] if names else ""
        if isinstance(value.get("id"), str):
            name = id_to_name.get(value["id"])
            if name is not None:
                return [name] if is_multi else name
        if isinstance(value.get("name"), str):
            name = value["name"]
            return [name] if is_multi else name
    return value


def normalize_row_cells_for_select_fields(cells: dict, select_lookup: dict) -> dict:
    normalized = dict(cells)
    for field_name, info in select_lookup.items():
        if field_name not in normalized:
            continue
        normalized[field_name] = normalize_select_value(
            normalized[field_name],
            info.get("type_key"),
            info.get("id_to_name") or {},
        )
    return normalized


def ensure_fields_from_template(client, token, workspace_id: str, db_id: str, fields: list[dict]) -> None:
    existing = grid_lib.get_database_fields(client, token, workspace_id, db_id)
    field_list = existing.get("data", []) if isinstance(existing, dict) else []
    by_name = {f.get("name"): f for f in field_list if isinstance(f, dict)}
    for field in fields or []:
        name = field.get("name")
        if not name:
            continue
        if name in by_name and by_name[name].get("id"):
            continue
        payload = dict(field)
        select_key = field_type_to_select_key(payload.get("field_type"))
        type_option_data = payload.get("type_option_data")
        if select_key:
            normalized_type_option_data = normalize_select_type_option_data(type_option_data)
            if normalized_type_option_data is not None:
                payload["type_option_data"] = normalized_type_option_data
        if (
            isinstance(type_option_data, dict)
            and type_option_data.get("database_id") == "<db_id_placeholder>"
        ):
            payload["type_option_data"] = {**type_option_data, "database_id": db_id}
        grid_lib.add_database_field(client, token, workspace_id, db_id, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a grid template to an existing document.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--env", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--gotrue-url", default=None)
    parser.add_argument("--client-version", default=None)
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--view-id", required=True)
    parser.add_argument("--template", default=None, help="Template JSON string")
    parser.add_argument("--template-file", default=None, help="Template JSON file")
    parser.add_argument("--clean-only", action="store_true", help="Only clean invalid blocks/rows.")
    args = parser.parse_args()

    client = build_client(args)
    token = resolve_token(args, client)
    template = load_template(args.template, args.template_file)

    grid_cfg = template.get("grid", {})
    grid_heading = grid_cfg.get("heading") or "Grid"
    grid_name = grid_cfg.get("name") or "Grid"
    clean_default_rows = bool(template.get("rules", {}).get("clean_default_rows", True))
    max_default_rows = int(template.get("rules", {}).get("max_default_rows", 3))

    doc_json = grid_lib.fetch_collab_json(
        client, token, args.workspace_id, args.view_id, grid_lib.DOC_COLLAB_TYPE
    )
    grid_section = grid_lib.select_grid_section(
        grid_lib.find_grid_sections(doc_json), heading_text=grid_heading
    )

    db_id = None
    db_view_id = None
    if grid_section:
        db_id = grid_section.get("parent_id")
        db_view_id = grid_section.get("view_id")

    if not db_id or not db_view_id:
        if args.clean_only:
            print_json(
                {
                    "workspace_id": args.workspace_id,
                    "view_id": args.view_id,
                    "note": "clean-only mode: grid not found, skipped creation",
                }
            )
            return 0
        resp = client.create_page_view(
            token,
            args.workspace_id,
            {"parent_view_id": args.view_id, "layout": grid_lib.GRID_LAYOUT, "name": grid_name},
        )
        data = resp.get("data") if isinstance(resp, dict) else None
        if not isinstance(data, dict):
            raise AppFlowyError("Failed to create grid page view", response=resp)
        db_id = data.get("database_id")
        db_view_id = data.get("view_id")
        if not db_id or not db_view_id:
            raise AppFlowyError("Missing database_id/view_id in create page response", response=resp)
        grid_lib.append_grid_section(
            client, token, args.workspace_id, args.view_id, grid_heading, db_id, db_view_id
        )

    removed_default_rows = []
    if clean_default_rows:
        removed_default_rows = grid_lib.cleanup_default_rows(
            client,
            token,
            args.workspace_id,
            db_id,
            max_remove=max_default_rows,
            view_ids=[db_view_id] if db_view_id else None,
        )

    if args.clean_only:
        print_json(
            {
                "workspace_id": args.workspace_id,
                "view_id": args.view_id,
                "grid_database_id": db_id,
                "grid_view_id": db_view_id,
                "default_rows_removed": removed_default_rows,
            }
        )
        return 0

    fields = template.get("fields") or []
    ensure_fields_from_template(client, token, args.workspace_id, db_id, fields)
    select_fields = build_select_field_updates(fields)
    grid_lib.repair_select_field_options(
        client, token, args.workspace_id, db_id, select_fields
    )
    latest_fields = grid_lib.get_database_fields(client, token, args.workspace_id, db_id)
    select_value_lookup = build_select_value_lookup(latest_fields)

    rows = template.get("rows") or []
    row_id_by_key: dict[str, str] = {}
    row_ids = []
    for row in rows:
        key = row.get("key")
        cells = row.get("cells") or {}
        if not key or not cells:
            continue
        cells = normalize_row_cells_for_select_fields(cells, select_value_lookup)
        pre_hash = f"{grid_name}:{key}"
        row_id = grid_lib.upsert_database_row(
            client, token, args.workspace_id, db_id, pre_hash, cells
        )
        row_id_by_key[key] = row_id
        row_ids.append(row_id)

    for row in rows:
        key = row.get("key")
        if not key:
            continue
        rel_cells = {}
        depends_on = row.get("depends_on") or []
        children = row.get("children") or []
        if depends_on:
            rel_cells["依赖"] = {
                "row_ids": [row_id_by_key[k] for k in depends_on if k in row_id_by_key]
            }
        if children:
            rel_cells["子项"] = {
                "row_ids": [row_id_by_key[k] for k in children if k in row_id_by_key]
            }
        if rel_cells:
            pre_hash = f"{grid_name}:{key}"
            grid_lib.upsert_database_row(
                client, token, args.workspace_id, db_id, pre_hash, rel_cells
            )

    print_json(
        {
            "workspace_id": args.workspace_id,
            "view_id": args.view_id,
            "grid_database_id": db_id,
            "grid_view_id": db_view_id,
            "default_rows_removed": removed_default_rows,
            "rows_upserted": len(row_ids),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
