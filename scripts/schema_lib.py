from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import doc_grid_lib as grid_lib
from appflowy_client import AppFlowyError

FIELD_TYPE_INT_TO_NAME = {
    0: "RichText",
    1: "Number",
    2: "DateTime",
    3: "SingleSelect",
    4: "MultiSelect",
    5: "Checkbox",
    6: "URL",
    7: "Checklist",
    8: "LastEditedTime",
    9: "CreatedTime",
    10: "Relation",
}


@dataclass
class NormalizedField:
    name: str
    name_key: str
    field_type: str
    field_type_raw: Any
    is_primary: bool
    field_id: str | None = None
    select_options: list[str] | None = None

    def to_public(self) -> dict[str, Any]:
        data = {
            "id": self.field_id,
            "name": self.name,
            "field_type": self.field_type,
            "is_primary": self.is_primary,
        }
        if self.select_options is not None:
            data["select_options"] = self.select_options
        return data


def normalize_field_type(value: Any) -> str:
    if isinstance(value, int):
        return FIELD_TYPE_INT_TO_NAME.get(value, str(value))
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            int_value = int(text)
            return FIELD_TYPE_INT_TO_NAME.get(int_value, text)
        return text
    return str(value)


def _parse_select_content(value: Any) -> dict[str, Any]:
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


def _normalize_option_names(options: Any) -> list[str]:
    if not isinstance(options, list):
        return []
    names: list[str] = []
    for item in options:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return sorted(names)


def _extract_select_options_from_current(type_option: Any) -> list[str]:
    if not isinstance(type_option, dict):
        return []

    parsed = _parse_select_content(type_option)
    if parsed.get("options"):
        return _normalize_option_names(parsed.get("options"))

    for key, value in type_option.items():
        if key == "content":
            continue
        if isinstance(value, dict):
            parsed = _parse_select_content(value)
            if parsed.get("options"):
                return _normalize_option_names(parsed.get("options"))
    return []


def _extract_select_options_from_target(type_option_data: Any) -> list[str]:
    if not isinstance(type_option_data, dict):
        return []
    parsed = _parse_select_content(type_option_data)
    if parsed.get("options"):
        return _normalize_option_names(parsed.get("options"))
    return []


def normalize_current_field(field: dict[str, Any]) -> NormalizedField:
    name = str(field.get("name") or "").strip()
    if not name:
        raise AppFlowyError(f"Invalid field without name: {field}")
    field_type = normalize_field_type(field.get("field_type"))
    select_options = None
    if field_type in {"SingleSelect", "MultiSelect"}:
        select_options = _extract_select_options_from_current(field.get("type_option"))
    return NormalizedField(
        name=name,
        name_key=name.lower(),
        field_type=field_type,
        field_type_raw=field.get("field_type"),
        is_primary=bool(field.get("is_primary")),
        field_id=field.get("id"),
        select_options=select_options,
    )


def normalize_target_field(field: dict[str, Any]) -> NormalizedField:
    name = str(field.get("name") or "").strip()
    if not name:
        raise AppFlowyError(f"Invalid target field without name: {field}")
    field_type = normalize_field_type(field.get("field_type"))
    select_options = None
    if field_type in {"SingleSelect", "MultiSelect"}:
        select_options = _extract_select_options_from_target(field.get("type_option_data"))
    return NormalizedField(
        name=name,
        name_key=name.lower(),
        field_type=field_type,
        field_type_raw=field.get("field_type"),
        is_primary=bool(field.get("is_primary", False)),
        field_id=field.get("id"),
        select_options=select_options,
    )


def extract_target_fields(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        fields = payload
    elif isinstance(payload, dict):
        fields = payload.get("fields")
    else:
        fields = None
    if not isinstance(fields, list):
        raise AppFlowyError("Target payload must be a JSON array or object with 'fields' array.")
    valid_fields = []
    for item in fields:
        if isinstance(item, dict):
            valid_fields.append(item)
    return valid_fields


def fetch_current_fields(client, token: str, workspace_id: str, database_id: str) -> list[NormalizedField]:
    resp = grid_lib.get_database_fields(client, token, workspace_id, database_id)
    fields = resp.get("data", []) if isinstance(resp, dict) else []
    result = []
    for field in fields:
        if isinstance(field, dict):
            result.append(normalize_current_field(field))
    return result


def fetch_target_fields_from_database(
    client, token: str, workspace_id: str, database_id: str
) -> list[NormalizedField]:
    return fetch_current_fields(client, token, workspace_id, database_id)


def normalize_target_fields(fields: list[dict[str, Any]]) -> list[NormalizedField]:
    return [normalize_target_field(item) for item in fields]


def _group_by_name(fields: list[NormalizedField]) -> dict[str, list[NormalizedField]]:
    grouped: dict[str, list[NormalizedField]] = {}
    for field in fields:
        grouped.setdefault(field.name_key, []).append(field)
    return grouped


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def build_schema_diff(
    source_fields: list[NormalizedField],
    target_fields: list[NormalizedField],
    *,
    rename_hint_threshold: float = 0.55,
) -> dict[str, Any]:
    exact_pairs: list[tuple[NormalizedField, NormalizedField]] = []
    matched_source_ids: set[str] = set()
    matched_target_ids: set[str] = set()

    source_by_id = {field.field_id: field for field in source_fields if field.field_id}
    for target_field in target_fields:
        if target_field.field_id and target_field.field_id in source_by_id:
            source_field = source_by_id[target_field.field_id]
            exact_pairs.append((source_field, target_field))
            matched_source_ids.add(source_field.field_id or "")
            matched_target_ids.add(target_field.field_id or "")

    source_remaining = [
        field for field in source_fields if not field.field_id or field.field_id not in matched_source_ids
    ]
    target_remaining = [
        field for field in target_fields if not field.field_id or field.field_id not in matched_target_ids
    ]

    source_by_name = _group_by_name(source_remaining)
    target_by_name = _group_by_name(target_remaining)

    source_duplicates = [items[0].name for items in source_by_name.values() if len(items) > 1]
    target_duplicates = [items[0].name for items in target_by_name.values() if len(items) > 1]

    unmatched_source: list[NormalizedField] = []
    unmatched_target: list[NormalizedField] = []

    for key, source_items in source_by_name.items():
        target_items = target_by_name.get(key, [])
        if len(source_items) == 1 and len(target_items) == 1:
            exact_pairs.append((source_items[0], target_items[0]))
        else:
            unmatched_source.extend(source_items)

    for key, target_items in target_by_name.items():
        source_items = source_by_name.get(key, [])
        if len(source_items) == 1 and len(target_items) == 1:
            continue
        unmatched_target.extend(target_items)

    type_changes = []
    select_option_changes = []
    for source_field, target_field in exact_pairs:
        if source_field.field_type != target_field.field_type:
            type_changes.append(
                {
                    "field_id": source_field.field_id,
                    "field_name": source_field.name,
                    "from_type": source_field.field_type,
                    "to_type": target_field.field_type,
                }
            )
        elif source_field.field_type in {"SingleSelect", "MultiSelect"}:
            source_options = source_field.select_options or []
            target_options = target_field.select_options or []
            if source_options != target_options:
                select_option_changes.append(
                    {
                        "field_id": source_field.field_id,
                        "field_name": source_field.name,
                        "field_type": source_field.field_type,
                        "from_options": source_options,
                        "to_options": target_options,
                    }
                )

    rename_candidates = []
    used_source_idx: set[int] = set()
    for target_idx, target_field in enumerate(unmatched_target):
        best_idx = None
        best_score = 0.0
        for source_idx, source_field in enumerate(unmatched_source):
            if source_idx in used_source_idx:
                continue
            if source_field.field_type != target_field.field_type:
                continue
            score = _similarity(source_field.name, target_field.name)
            if score > best_score:
                best_score = score
                best_idx = source_idx
        if best_idx is not None and best_score >= rename_hint_threshold:
            source_field = unmatched_source[best_idx]
            if source_field.name_key == target_field.name_key:
                continue
            used_source_idx.add(best_idx)
            rename_candidates.append(
                {
                    "field_id": source_field.field_id,
                    "from_name": source_field.name,
                    "to_name": target_field.name,
                    "field_type": source_field.field_type,
                    "confidence": round(best_score, 3),
                }
            )

    matched_source_ids = {item["field_id"] for item in rename_candidates if item.get("field_id")}
    matched_target_names = {item["to_name"].lower() for item in rename_candidates if item.get("to_name")}

    add_fields = [
        field.to_public()
        for field in unmatched_target
        if field.name_key not in matched_target_names
    ]
    delete_fields = [
        field.to_public()
        for field in unmatched_source
        if field.field_id not in matched_source_ids
    ]

    return {
        "source_field_count": len(source_fields),
        "target_field_count": len(target_fields),
        "source_duplicate_names": sorted(source_duplicates),
        "target_duplicate_names": sorted(target_duplicates),
        "add_fields": add_fields,
        "delete_fields": delete_fields,
        "rename_candidates": rename_candidates,
        "type_changes": type_changes,
        "select_option_changes": select_option_changes,
    }


def build_migration_plan(
    diff_result: dict[str, Any],
    *,
    rename_apply_threshold: float = 0.75,
) -> dict[str, Any]:
    operations = []
    blocked = []

    for item in diff_result.get("add_fields", []) or []:
        operations.append(
            {
                "op": "add_field",
                "field_name": item.get("name"),
                "field_type": item.get("field_type"),
                "risk": "low",
                "auto_executable": True,
            }
        )

    for item in diff_result.get("rename_candidates", []) or []:
        confidence = float(item.get("confidence", 0.0))
        auto = confidence >= rename_apply_threshold
        operations.append(
            {
                "op": "rename_field",
                "field_id": item.get("field_id"),
                "from_name": item.get("from_name"),
                "to_name": item.get("to_name"),
                "field_type": item.get("field_type"),
                "confidence": confidence,
                "risk": "medium" if auto else "high",
                "auto_executable": auto,
                "note": None if auto else "Rename confidence too low; manual review required.",
            }
        )

    for item in diff_result.get("delete_fields", []) or []:
        is_primary = bool(item.get("is_primary"))
        op = {
            "op": "delete_field",
            "field_id": item.get("id"),
            "field_name": item.get("name"),
            "field_type": item.get("field_type"),
            "risk": "high",
            "auto_executable": not is_primary,
        }
        if is_primary:
            op["note"] = "Primary field deletion is blocked."
            blocked.append(op)
        operations.append(op)

    for item in diff_result.get("type_changes", []) or []:
        op = {
            "op": "change_field_type",
            "field_id": item.get("field_id"),
            "field_name": item.get("field_name"),
            "from_type": item.get("from_type"),
            "to_type": item.get("to_type"),
            "risk": "high",
            "auto_executable": False,
            "note": "Type migration requires manual review and dedicated migration workflow.",
        }
        operations.append(op)
        blocked.append(op)

    for item in diff_result.get("select_option_changes", []) or []:
        operations.append(
            {
                "op": "update_select_options",
                "field_id": item.get("field_id"),
                "field_name": item.get("field_name"),
                "field_type": item.get("field_type"),
                "risk": "medium",
                "auto_executable": True,
            }
        )

    op_counts: dict[str, int] = {}
    for op in operations:
        key = op.get("op", "unknown")
        op_counts[key] = op_counts.get(key, 0) + 1

    requires_manual_review = any(not bool(op.get("auto_executable")) for op in operations)
    high_risk_count = sum(1 for op in operations if op.get("risk") == "high")

    return {
        "operations": operations,
        "op_counts": op_counts,
        "blocked_operations": blocked,
        "requires_manual_review": requires_manual_review,
        "requires_confirmation": high_risk_count > 0,
        "high_risk_count": high_risk_count,
    }
