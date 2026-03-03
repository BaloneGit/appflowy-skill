from __future__ import annotations

import json
from typing import Any

import doc_grid_lib as grid_lib


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


def plan_missing_fields(
    current_fields: list[dict],
    template_fields: list[dict],
) -> list[dict]:
    by_name = {f.get("name"): f for f in current_fields if isinstance(f, dict)}
    planned: list[dict] = []
    for field in template_fields or []:
        if not isinstance(field, dict):
            continue
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
        planned.append(payload)
    return planned


def ensure_fields_from_template(
    client,
    token: str,
    workspace_id: str,
    database_id: str,
    template_fields: list[dict],
) -> list[str]:
    existing = grid_lib.get_database_fields(client, token, workspace_id, database_id)
    field_list = existing.get("data", []) if isinstance(existing, dict) else []
    pending = plan_missing_fields(field_list, template_fields)
    created_ids: list[str] = []
    for payload in pending:
        created_ids.append(
            grid_lib.add_database_field(client, token, workspace_id, database_id, payload)
        )
    return created_ids


def plan_select_option_repairs(current_fields: list[dict], template_fields: list[dict]) -> list[dict]:
    desired_by_name = {}
    for field in build_select_field_updates(template_fields):
        name = field.get("name")
        options = field.get("options") or []
        if not name or not options:
            continue
        desired_by_name[name] = {
            "options": options,
            "disable_color": bool(field.get("disable_color", False)),
        }

    planned: list[dict] = []
    for field in current_fields:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if not name or name not in desired_by_name:
            continue
        type_key = field_type_to_select_key(field.get("field_type"))
        if not type_key:
            continue
        type_option = field.get("type_option") or {}
        current = {}
        if isinstance(type_option, dict):
            current = parse_select_content(type_option)
            if not current.get("options"):
                typed = type_option.get(type_key)
                if isinstance(typed, dict):
                    current = parse_select_content(typed)
        current_options = current.get("options") or []
        current_disable = bool(current.get("disable_color", False))
        desired_options = desired_by_name[name]["options"]
        desired_disable = desired_by_name[name]["disable_color"]
        if current_options != desired_options or current_disable != desired_disable:
            planned.append(
                {
                    "field_id": field.get("id"),
                    "field_name": name,
                    "type_key": type_key,
                    "content": json.dumps(
                        {
                            "options": desired_options,
                            "disable_color": desired_disable,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
    return planned


def repair_select_options_from_template(
    client,
    token: str,
    workspace_id: str,
    database_id: str,
    template_fields: list[dict],
) -> list[str]:
    fields_resp = grid_lib.get_database_fields(client, token, workspace_id, database_id)
    current_fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []
    updates = plan_select_option_repairs(current_fields, template_fields)
    if not updates:
        return []
    doc_state, state_vector = grid_lib.fetch_collab_state(
        client, token, workspace_id, database_id, grid_lib.DB_COLLAB_TYPE
    )
    update = grid_lib.run_node_update_select_options(doc_state, state_vector, updates)
    grid_lib.post_web_update(client, token, workspace_id, database_id, grid_lib.DB_COLLAB_TYPE, update)
    return [item.get("field_id") for item in updates if item.get("field_id")]
