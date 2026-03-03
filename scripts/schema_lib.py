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
FIELD_TYPE_NAME_TO_INT = {name: key for key, name in FIELD_TYPE_INT_TO_NAME.items()}


@dataclass
class NormalizedField:
    name: str
    name_key: str
    field_type: str
    field_type_raw: Any
    is_primary: bool
    field_id: str | None = None
    select_options: list[str] | None = None
    select_options_detail: list[dict[str, Any]] | None = None
    select_disable_color: bool | None = None
    type_option_data: dict[str, Any] | None = None
    target_payload: dict[str, Any] | None = None

    def to_public(self) -> dict[str, Any]:
        data = {
            "id": self.field_id,
            "name": self.name,
            "field_type": self.field_type,
            "field_type_raw": self.field_type_raw,
            "is_primary": self.is_primary,
        }
        if self.select_options is not None:
            data["select_options"] = self.select_options
        if self.select_options_detail is not None:
            data["select_options_detail"] = self.select_options_detail
        if self.select_disable_color is not None:
            data["select_disable_color"] = self.select_disable_color
        if self.type_option_data is not None:
            data["type_option_data"] = self.type_option_data
        if self.target_payload is not None:
            data["target_payload"] = self.target_payload
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


def _normalize_option_details(options: Any) -> list[dict[str, Any]]:
    if not isinstance(options, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in options:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            normalized.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": name.strip(),
                    "color": item.get("color"),
                }
            )
    return sorted(normalized, key=lambda it: (str(it.get("name") or "").lower(), str(it.get("id") or "")))


def _extract_option_names(option_details: list[dict[str, Any]]) -> list[str]:
    return sorted(
        [str(item.get("name")).strip() for item in option_details if str(item.get("name") or "").strip()]
    )


def _normalize_select_type_option_data(type_option_data: Any) -> dict[str, Any] | None:
    if not isinstance(type_option_data, dict):
        return None
    parsed = _parse_select_content(type_option_data)
    if parsed.get("options"):
        payload = {
            "options": parsed.get("options") or [],
            "disable_color": bool(parsed.get("disable_color", False)),
        }
        return {"content": json.dumps(payload, ensure_ascii=False)}
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


def _extract_select_info_from_current(
    type_option: Any,
) -> tuple[list[str], list[dict[str, Any]], bool]:
    if not isinstance(type_option, dict):
        return [], [], False

    parsed = _parse_select_content(type_option)
    if parsed.get("options"):
        details = _normalize_option_details(parsed.get("options"))
        return _extract_option_names(details), details, bool(parsed.get("disable_color", False))

    for key, value in type_option.items():
        if key == "content":
            continue
        if isinstance(value, dict):
            parsed = _parse_select_content(value)
            if parsed.get("options"):
                details = _normalize_option_details(parsed.get("options"))
                return _extract_option_names(details), details, bool(parsed.get("disable_color", False))
    return [], [], False


def _extract_select_info_from_target(
    type_option_data: Any,
) -> tuple[list[str], list[dict[str, Any]], bool]:
    if not isinstance(type_option_data, dict):
        return [], [], False
    parsed = _parse_select_content(type_option_data)
    if parsed.get("options"):
        details = _normalize_option_details(parsed.get("options"))
        return _extract_option_names(details), details, bool(parsed.get("disable_color", False))
    return [], [], False


def _build_target_payload_from_template_field(field: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "name": field.get("name"),
        "field_type": field.get("field_type"),
    }
    if "type_option_data" in field:
        normalized = _normalize_select_type_option_data(field.get("type_option_data"))
        payload["type_option_data"] = normalized if normalized is not None else field.get("type_option_data")
    return payload


def _build_target_payload_from_database_field(field: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": field.get("name"),
        "field_type": field.get("field_type"),
    }
    field_type_name = normalize_field_type(field.get("field_type"))
    if field_type_name in {"SingleSelect", "MultiSelect"}:
        _, details, disable_color = _extract_select_info_from_current(field.get("type_option"))
        if details:
            payload["type_option_data"] = {
                "content": json.dumps(
                    {"options": details, "disable_color": bool(disable_color)},
                    ensure_ascii=False,
                )
            }
    elif field_type_name == "Relation":
        type_option = field.get("type_option")
        if isinstance(type_option, dict):
            rel_database_id = type_option.get("database_id")
            if rel_database_id:
                payload["type_option_data"] = {"database_id": rel_database_id}
    return payload


def normalize_current_field(field: dict[str, Any]) -> NormalizedField:
    name = str(field.get("name") or "").strip()
    if not name:
        raise AppFlowyError(f"Invalid field without name: {field}")
    field_type = normalize_field_type(field.get("field_type"))
    select_options = None
    select_options_detail = None
    select_disable_color = None
    if field_type in {"SingleSelect", "MultiSelect"}:
        select_options, select_options_detail, select_disable_color = _extract_select_info_from_current(
            field.get("type_option")
        )
    return NormalizedField(
        name=name,
        name_key=name.lower(),
        field_type=field_type,
        field_type_raw=field.get("field_type"),
        is_primary=bool(field.get("is_primary")),
        field_id=field.get("id"),
        select_options=select_options,
        select_options_detail=select_options_detail,
        select_disable_color=select_disable_color,
    )


def normalize_target_field(field: dict[str, Any]) -> NormalizedField:
    name = str(field.get("name") or "").strip()
    if not name:
        raise AppFlowyError(f"Invalid target field without name: {field}")
    field_type = normalize_field_type(field.get("field_type"))
    select_options = None
    select_options_detail = None
    select_disable_color = None
    type_option_data = None
    if field_type in {"SingleSelect", "MultiSelect"}:
        type_option_data = _normalize_select_type_option_data(field.get("type_option_data"))
        select_options, select_options_detail, select_disable_color = _extract_select_info_from_target(
            type_option_data or field.get("type_option_data")
        )
    return NormalizedField(
        name=name,
        name_key=name.lower(),
        field_type=field_type,
        field_type_raw=field.get("field_type"),
        is_primary=bool(field.get("is_primary", False)),
        field_id=field.get("id"),
        select_options=select_options,
        select_options_detail=select_options_detail,
        select_disable_color=select_disable_color,
        type_option_data=type_option_data,
        target_payload=_build_target_payload_from_template_field(field),
    )


def normalize_target_field_from_database(field: dict[str, Any]) -> NormalizedField:
    name = str(field.get("name") or "").strip()
    if not name:
        raise AppFlowyError(f"Invalid target field without name: {field}")
    field_type = normalize_field_type(field.get("field_type"))
    select_options = None
    select_options_detail = None
    select_disable_color = None
    type_option_data = None
    if field_type in {"SingleSelect", "MultiSelect"}:
        select_options, select_options_detail, select_disable_color = _extract_select_info_from_current(
            field.get("type_option")
        )
        if select_options_detail:
            type_option_data = {
                "content": json.dumps(
                    {
                        "options": select_options_detail,
                        "disable_color": bool(select_disable_color),
                    },
                    ensure_ascii=False,
                )
            }
    elif field_type == "Relation":
        type_option = field.get("type_option")
        if isinstance(type_option, dict) and type_option.get("database_id"):
            type_option_data = {"database_id": type_option.get("database_id")}

    return NormalizedField(
        name=name,
        name_key=name.lower(),
        field_type=field_type,
        field_type_raw=field.get("field_type"),
        is_primary=bool(field.get("is_primary", False)),
        field_id=field.get("id"),
        select_options=select_options,
        select_options_detail=select_options_detail,
        select_disable_color=select_disable_color,
        type_option_data=type_option_data,
        target_payload=_build_target_payload_from_database_field(field),
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
    resp = grid_lib.get_database_fields(client, token, workspace_id, database_id)
    fields = resp.get("data", []) if isinstance(resp, dict) else []
    result = []
    for field in fields:
        if isinstance(field, dict):
            result.append(normalize_target_field_from_database(field))
    return result


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
                        "from_options_detail": source_field.select_options_detail or [],
                        "from_disable_color": bool(source_field.select_disable_color),
                        "to_options": target_options,
                        "to_options_detail": target_field.select_options_detail or [],
                        "to_disable_color": bool(target_field.select_disable_color),
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
        target_payload = item.get("target_payload")
        auto_executable = bool(target_payload)
        risk = "low"
        note = None
        if item.get("field_type") == "Relation":
            auto_executable = False
            risk = "high"
            note = "Relation field migration requires manual review."
        if not target_payload:
            auto_executable = False
            risk = "high"
            note = "Missing target payload for field creation."
        if isinstance(target_payload, dict):
            type_option_data = target_payload.get("type_option_data")
            if (
                isinstance(type_option_data, dict)
                and str(type_option_data.get("database_id") or "") == "<db_id_placeholder>"
            ):
                auto_executable = False
                risk = "high"
                note = "Relation field has unresolved database_id placeholder."
        operations.append(
            {
                "op": "add_field",
                "field_name": item.get("name"),
                "field_type": item.get("field_type"),
                "field_data": item,
                "risk": risk,
                "auto_executable": auto_executable,
                "note": note,
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
                "to_options": item.get("to_options", []),
                "to_options_detail": item.get("to_options_detail", []),
                "to_disable_color": bool(item.get("to_disable_color", False)),
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
