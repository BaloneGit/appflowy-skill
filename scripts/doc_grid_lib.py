import json
import subprocess
from pathlib import Path

from appflowy_client import AppFlowyError

DOC_COLLAB_TYPE = 0
DB_COLLAB_TYPE = 1
GRID_LAYOUT = 1


def fetch_collab_json(client, token: str, workspace_id: str, object_id: str, collab_type: int) -> dict:
    base = client._require_base_url()
    return client._request_json(
        "GET",
        f"{base}/api/workspace/v1/{workspace_id}/collab/{object_id}/json",
        token=token,
        params={"collab_type": collab_type},
    )


def fetch_collab_state(
    client, token: str, workspace_id: str, object_id: str, collab_type: int
) -> tuple[list[int], list[int]]:
    base = client._require_base_url()
    payload = client._request_json(
        "GET",
        f"{base}/api/workspace/v1/{workspace_id}/collab/{object_id}",
        token=token,
        params={"collab_type": collab_type},
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not data or "doc_state" not in data:
        raise AppFlowyError("Missing doc_state in collab response", response=payload)
    return data["doc_state"], data.get("state_vector", [])


def post_web_update(
    client, token: str, workspace_id: str, object_id: str, collab_type: int, update: list[int]
) -> dict:
    base = client._require_base_url()
    payload = {"doc_state": update, "collab_type": collab_type}
    return client._request_json(
        "POST",
        f"{base}/api/workspace/v1/{workspace_id}/collab/{object_id}/web-update",
        token=token,
        json_body=payload,
    )


def list_databases(client, token: str, workspace_id: str) -> dict:
    return client.list_databases(token, workspace_id)


def get_database_fields(client, token: str, workspace_id: str, database_id: str) -> dict:
    base = client._require_base_url()
    return client._request_json(
        "GET",
        f"{base}/api/workspace/{workspace_id}/database/{database_id}/fields",
        token=token,
    )


def add_database_field(client, token: str, workspace_id: str, database_id: str, field: dict) -> str:
    base = client._require_base_url()
    resp = client._request_json(
        "POST",
        f"{base}/api/workspace/{workspace_id}/database/{database_id}/fields",
        token=token,
        json_body=field,
    )
    data = resp.get("data") if isinstance(resp, dict) else None
    if not data:
        raise AppFlowyError("Failed to add database field", response=resp)
    return data


def add_database_row(client, token: str, workspace_id: str, database_id: str, cells: dict) -> str:
    base = client._require_base_url()
    resp = client._request_json(
        "POST",
        f"{base}/api/workspace/{workspace_id}/database/{database_id}/row",
        token=token,
        json_body={"cells": cells},
    )
    data = resp.get("data") if isinstance(resp, dict) else None
    if not data:
        raise AppFlowyError("Failed to add database row", response=resp)
    return data


def upsert_database_row(
    client,
    token: str,
    workspace_id: str,
    database_id: str,
    pre_hash: str,
    cells: dict,
) -> str:
    base = client._require_base_url()
    resp = client._request_json(
        "PUT",
        f"{base}/api/workspace/{workspace_id}/database/{database_id}/row",
        token=token,
        json_body={"pre_hash": pre_hash, "cells": cells},
    )
    data = resp.get("data") if isinstance(resp, dict) else None
    if not data:
        raise AppFlowyError("Failed to upsert database row", response=resp)
    return data


def list_row_ids(client, token: str, workspace_id: str, database_id: str) -> list[str]:
    base = client._require_base_url()
    resp = client._request_json(
        "GET",
        f"{base}/api/workspace/{workspace_id}/database/{database_id}/row",
        token=token,
    )
    rows = resp.get("data") if isinstance(resp, dict) else []
    ids = []
    for item in rows or []:
        row_id = item.get("id") if isinstance(item, dict) else None
        if row_id:
            ids.append(row_id)
    return ids


def get_row_details(client, token: str, workspace_id: str, database_id: str, row_ids: list[str]) -> list[dict]:
    if not row_ids:
        return []
    base = client._require_base_url()
    resp = client._request_json(
        "GET",
        f"{base}/api/workspace/{workspace_id}/database/{database_id}/row/detail",
        token=token,
        params={"ids": ",".join(row_ids)},
    )
    return resp.get("data") if isinstance(resp, dict) else []


def create_database_view(client, token: str, workspace_id: str, view_id: str, name: str) -> None:
    base = client._require_base_url()
    try:
        client._request_json(
            "POST",
            f"{base}/api/workspace/{workspace_id}/page-view/{view_id}/database-view",
            token=token,
            json_body={"layout": GRID_LAYOUT, "name": name},
        )
        return
    except AppFlowyError as exc:
        message = str(exc)
        response = getattr(exc, "response", None)
        if response and b"parent_view_id" in response:
            pass
        elif "parent_view_id" in message:
            pass
        else:
            raise

    client.create_page_view(
        token,
        workspace_id,
        {"parent_view_id": view_id, "layout": GRID_LAYOUT, "name": name},
    )


def append_grid_section(
    client, token: str, workspace_id: str, view_id: str, heading: str, db_id: str, db_view_id: str
) -> dict:
    payload = {
        "blocks": [
            {"type": "heading", "data": {"level": 2, "delta": [{"insert": heading}]}},
            {"type": "grid", "data": {"parent_id": db_id, "view_id": db_view_id}},
        ]
    }
    return client.append_block(token, workspace_id, view_id, payload)


def parse_table_rows(blocks: dict, children_map: dict, text_map: dict, table_id: str) -> list[list[str]]:
    def children_of(block_id: str) -> list[str]:
        block = blocks.get(block_id)
        if not block:
            return []
        return children_map.get(block.get("children"), [])

    def paragraph_text(paragraph_id: str) -> str:
        block = blocks.get(paragraph_id)
        if not block:
            return ""
        ext_id = block.get("external_id")
        if not ext_id:
            return ""
        return text_map.get(ext_id, "")

    def cell_text(cell_id: str) -> str:
        for child_id in children_of(cell_id):
            child = blocks.get(child_id)
            if child and child.get("ty") == "paragraph":
                return paragraph_text(child_id).replace("\n", "").strip()
        return ""

    rows = []
    for row_id in children_of(table_id):
        row_cells = [cell_text(cell_id) for cell_id in children_of(row_id)]
        rows.append(row_cells)
    return rows


def block_text(blocks: dict, children_map: dict, text_map: dict, block_id: str) -> str:
    block = blocks.get(block_id)
    if not isinstance(block, dict):
        return ""
    ext_id = block.get("external_id")
    if ext_id and ext_id in text_map:
        return str(text_map.get(ext_id, "")).replace("\n", "").strip()
    children_key = block.get("children")
    if not children_key:
        return ""
    child_ids = children_map.get(children_key, [])
    for child_id in child_ids:
        child = blocks.get(child_id)
        if isinstance(child, dict) and child.get("external_id"):
            text = text_map.get(child.get("external_id"), "")
            if text:
                return str(text).replace("\n", "").strip()
    return ""


def page_children_order(doc: dict) -> list[str]:
    page_id = doc.get("page_id")
    meta = doc.get("meta", {})
    children_map = meta.get("children_map", {})
    if not page_id:
        return []
    return children_map.get(page_id, [])


def find_grid_sections(doc_json: dict) -> list[dict]:
    doc = doc_json.get("data", {}).get("collab", {}).get("document", {})
    blocks = doc.get("blocks", {})
    meta = doc.get("meta", {})
    children_map = meta.get("children_map", {})
    text_map = meta.get("text_map", {})
    ordered = page_children_order(doc)

    sections = []
    current_heading = {"id": None, "text": ""}
    for block_id in ordered:
        block = blocks.get(block_id)
        if not isinstance(block, dict):
            continue
        if block.get("ty") == "heading":
            current_heading = {
                "id": block_id,
                "text": block_text(blocks, children_map, text_map, block_id),
            }
            continue
        if block.get("ty") != "grid":
            continue
        raw_data = block.get("data")
        if isinstance(raw_data, str):
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                data = {}
        elif isinstance(raw_data, dict):
            data = raw_data
        else:
            data = {}
        sections.append(
            {
                "block_id": block_id,
                "parent_id": data.get("parent_id"),
                "view_id": data.get("view_id"),
                "heading_id": current_heading.get("id"),
                "heading_text": current_heading.get("text", ""),
            }
        )
    return sections


def select_grid_section(sections: list[dict], heading_text: str | None = None) -> dict | None:
    if not sections:
        return None
    if heading_text:
        matched = None
        for item in sections:
            if item.get("heading_text") == heading_text:
                matched = item
        if matched:
            return matched
    return sections[0]


def run_node_delete_doc_blocks(
    doc_state: list[int], state_vector: list[int], delete_block_ids: list[str]
) -> list[int]:
    script_path = Path(__file__).resolve().parent / "collab_delete_blocks.mjs"
    if not script_path.exists():
        raise AppFlowyError(f"Missing script: {script_path}")

    payload = json.dumps(
        {
            "doc_state": doc_state,
            "state_vector": state_vector,
            "delete_block_ids": delete_block_ids,
        }
    )
    result = subprocess.run(
        ["node", str(script_path)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AppFlowyError(f"Node script failed: {result.stderr.strip()}")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppFlowyError("Failed to parse Node output") from exc
    update = output.get("update")
    if not isinstance(update, list):
        raise AppFlowyError("Node output missing update")
    return update


def run_node_delete_row_orders(
    doc_state: list[int],
    state_vector: list[int],
    row_ids: list[str],
    view_ids: list[str] | None = None,
) -> list[int]:
    script_path = Path(__file__).resolve().parent / "collab_delete_row_orders.mjs"
    if not script_path.exists():
        raise AppFlowyError(f"Missing script: {script_path}")

    payload = json.dumps(
        {
            "doc_state": doc_state,
            "state_vector": state_vector,
            "row_ids": row_ids,
            "view_ids": view_ids or [],
        }
    )
    result = subprocess.run(
        ["node", str(script_path)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AppFlowyError(f"Node script failed: {result.stderr.strip()}")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppFlowyError("Failed to parse Node output") from exc
    update = output.get("update")
    if not isinstance(update, list):
        raise AppFlowyError("Node output missing update")
    return update


def run_node_update_select_options(
    doc_state: list[int],
    state_vector: list[int],
    field_updates: list[dict],
) -> list[int]:
    script_path = Path(__file__).resolve().parent / "collab_update_select_options.mjs"
    if not script_path.exists():
        raise AppFlowyError(f"Missing script: {script_path}")

    payload = json.dumps(
        {
            "doc_state": doc_state,
            "state_vector": state_vector,
            "field_updates": field_updates,
        }
    )
    result = subprocess.run(
        ["node", str(script_path)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AppFlowyError(f"Node script failed: {result.stderr.strip()}")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppFlowyError("Failed to parse Node output") from exc
    update = output.get("update")
    if not isinstance(update, list):
        raise AppFlowyError("Node output missing update")
    return update


def run_node_rename_fields(
    doc_state: list[int],
    state_vector: list[int],
    renames: list[dict],
) -> list[int]:
    script_path = Path(__file__).resolve().parent / "collab_rename_fields.mjs"
    if not script_path.exists():
        raise AppFlowyError(f"Missing script: {script_path}")

    payload = json.dumps(
        {
            "doc_state": doc_state,
            "state_vector": state_vector,
            "renames": renames,
        }
    )
    result = subprocess.run(
        ["node", str(script_path)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AppFlowyError(f"Node script failed: {result.stderr.strip()}")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppFlowyError("Failed to parse Node output") from exc
    update = output.get("update")
    if not isinstance(update, list):
        raise AppFlowyError("Node output missing update")
    return update


def run_node_delete_fields(
    doc_state: list[int],
    state_vector: list[int],
    field_ids: list[str],
) -> list[int]:
    script_path = Path(__file__).resolve().parent / "collab_delete_fields.mjs"
    if not script_path.exists():
        raise AppFlowyError(f"Missing script: {script_path}")

    payload = json.dumps(
        {
            "doc_state": doc_state,
            "state_vector": state_vector,
            "field_ids": field_ids,
        }
    )
    result = subprocess.run(
        ["node", str(script_path)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AppFlowyError(f"Node script failed: {result.stderr.strip()}")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppFlowyError("Failed to parse Node output") from exc
    update = output.get("update")
    if not isinstance(update, list):
        raise AppFlowyError("Node output missing update")
    return update


def is_empty_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, bool):
        return value is False
    if isinstance(value, (int, float)):
        return False
    if isinstance(value, list):
        if len(value) == 0:
            return True
        return all(is_empty_value(item) for item in value)
    if isinstance(value, dict):
        if not value:
            return True
        if "options" in value and "selected_option_ids" in value:
            return not value.get("options") and not value.get("selected_option_ids")
        if "files" in value:
            files = value.get("files")
            return not files
        if "start" in value and "end" in value:
            return not value.get("start") and not value.get("end")
    return False


def is_row_empty(cells: dict, ignore_fields: set[str]) -> bool:
    for name, value in cells.items():
        if name in ignore_fields:
            continue
        if not is_empty_value(value):
            return False
    return True


def cleanup_default_rows(
    client,
    token: str,
    workspace_id: str,
    database_id: str,
    *,
    max_remove: int = 3,
    view_ids: list[str] | None = None,
) -> list[str]:
    row_ids = list_row_ids(client, token, workspace_id, database_id)
    if not row_ids:
        return []

    row_details = get_row_details(client, token, workspace_id, database_id, row_ids)
    ignore_fields = {
        "Done",
        "Created time",
        "Last edited time",
        "Created Time",
        "Last Edited Time",
        "Assignee",
    }
    fields_resp = get_database_fields(client, token, workspace_id, database_id)
    fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []
    system_field_types = {
        "CreatedTime",
        "LastEditedTime",
        "CreatedBy",
        "LastEditedBy",
        "Person",
    }
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        field_type = field.get("field_type")
        if name and isinstance(field_type, str) and field_type in system_field_types:
            ignore_fields.add(name)
    empty_ids = []
    for row in row_details:
        row_id = row.get("id") if isinstance(row, dict) else None
        cells = row.get("cells") if isinstance(row, dict) else None
        if not row_id or not isinstance(cells, dict):
            continue
        if is_row_empty(cells, ignore_fields):
            empty_ids.append(row_id)
    if not empty_ids:
        return []

    empty_ids = empty_ids[:max_remove]
    doc_state, state_vector = fetch_collab_state(
        client, token, workspace_id, database_id, DB_COLLAB_TYPE
    )
    update = run_node_delete_row_orders(doc_state, state_vector, empty_ids, view_ids=view_ids)
    post_web_update(client, token, workspace_id, database_id, DB_COLLAB_TYPE, update)
    return empty_ids


def build_select_content(options: list[dict], disable_color: bool = False) -> str:
    payload = {"options": options, "disable_color": disable_color}
    return json.dumps(payload, ensure_ascii=False)


def _field_type_to_select_key(field_type: object) -> str | None:
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


def _parse_select_content(value: object) -> dict:
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
            return _parse_select_content(value.get("content"))
    return {}


def _extract_select_content_from_type_option(type_option: object, type_key: str) -> dict:
    if not isinstance(type_option, dict):
        return {}
    candidates = []
    if "content" in type_option:
        candidates.append(type_option.get("content"))
    typed = type_option.get(type_key)
    if isinstance(typed, dict):
        candidates.append(typed.get("content"))
        candidates.append(typed)
    candidates.append(type_option)
    for candidate in candidates:
        parsed = _parse_select_content(candidate)
        if parsed.get("options"):
            return parsed
    return {}


def _normalize_options(options: object) -> list[dict]:
    if not isinstance(options, list):
        return []
    normalized = []
    for option in options:
        if not isinstance(option, dict):
            continue
        normalized.append(
            {
                "id": option.get("id", ""),
                "name": option.get("name", ""),
                "color": option.get("color"),
            }
        )
    return normalized


def repair_select_field_options(
    client,
    token: str,
    workspace_id: str,
    database_id: str,
    select_fields: list[dict],
) -> list[str]:
    if not select_fields:
        return []

    desired_by_name = {}
    for field in select_fields:
        name = field.get("name")
        options = _normalize_options(field.get("options"))
        if name and options:
            disable_color = bool(field.get("disable_color", False))
            desired_by_name[name] = {
                "content": build_select_content(options, disable_color),
                "options": options,
                "disable_color": disable_color,
            }

    fields_resp = get_database_fields(client, token, workspace_id, database_id)
    fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []

    updates = []
    for field in fields:
        name = field.get("name")
        field_type = field.get("field_type")
        if not name or name not in desired_by_name:
            continue
        type_key = _field_type_to_select_key(field_type)
        if not type_key:
            continue
        type_option = field.get("type_option") or {}
        current = _extract_select_content_from_type_option(type_option, type_key)
        current_options = _normalize_options(current.get("options"))
        desired_options = desired_by_name[name]["options"]
        current_disable_color = bool(current.get("disable_color", False))
        desired_disable_color = desired_by_name[name]["disable_color"]
        if current_options != desired_options or current_disable_color != desired_disable_color:
            updates.append(
                {
                    "field_id": field.get("id"),
                    "type_key": type_key,
                    "content": desired_by_name[name]["content"],
                }
            )

    if not updates:
        return []

    doc_state, state_vector = fetch_collab_state(
        client, token, workspace_id, database_id, DB_COLLAB_TYPE
    )
    update = run_node_update_select_options(doc_state, state_vector, updates)
    post_web_update(client, token, workspace_id, database_id, DB_COLLAB_TYPE, update)
    return [item.get("field_id") for item in updates if item.get("field_id")]
