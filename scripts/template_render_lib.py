from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from _common import load_json_payload
from appflowy_client import AppFlowyError

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
FULL_PLACEHOLDER_PATTERN = re.compile(r"^\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}$")
SUPPORTED_VAR_TYPES = {"string", "number", "boolean", "array", "object"}


def load_template_payload(template: str | None, template_file: str | None) -> dict[str, Any]:
    payload = load_json_payload(template, template_file)
    if not isinstance(payload, dict):
        raise AppFlowyError("Template payload must be a JSON object.")
    return payload


def load_vars_payload(vars_json: str | None, vars_file: str | None) -> dict[str, Any]:
    if not vars_json and not vars_file:
        return {}
    payload = load_json_payload(vars_json, vars_file)
    if not isinstance(payload, dict):
        raise AppFlowyError("Variables payload must be a JSON object.")
    return payload


def _canonical_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _get_by_path(values: dict[str, Any], path: str) -> Any:
    if path in values:
        return values[path]
    current: Any = values
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise AppFlowyError(f"Template variable not found: {path}")
        current = current[segment]
    return current


def normalize_template_var_spec(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = template.get("template_vars")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AppFlowyError("template_vars must be an object.")
    spec: dict[str, dict[str, Any]] = {}
    for name, config in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise AppFlowyError("template_vars key must be non-empty string.")
        var_name = name.strip()
        if isinstance(config, dict):
            item = dict(config)
        else:
            item = {"default": config}
        if "required" not in item:
            item["required"] = False
        item["required"] = bool(item["required"])
        type_name = item.get("type")
        if type_name is not None:
            if not isinstance(type_name, str):
                raise AppFlowyError(f"template_vars.{var_name}.type must be a string.")
            normalized_type = type_name.strip().lower()
            if normalized_type not in SUPPORTED_VAR_TYPES:
                raise AppFlowyError(
                    f"template_vars.{var_name}.type unsupported: {type_name}. "
                    f"Supported: {sorted(SUPPORTED_VAR_TYPES)}"
                )
            item["type"] = normalized_type
        spec[var_name] = item
    return spec


def resolve_template_vars(
    template: dict[str, Any],
    provided_vars: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = normalize_template_var_spec(template)
    resolved = dict(provided_vars)
    missing_required: list[str] = []
    defaulted: list[str] = []
    type_errors: list[str] = []

    for name, item in spec.items():
        has_value = name in resolved
        if not has_value and "default" in item:
            resolved[name] = item.get("default")
            defaulted.append(name)
            has_value = True
        if item.get("required") and not has_value:
            missing_required.append(name)
            continue
        if has_value and item.get("type"):
            actual = _canonical_type(resolved.get(name))
            expected = item.get("type")
            if actual != expected:
                type_errors.append(f"{name}: expected {expected}, got {actual}")

    if missing_required:
        raise AppFlowyError(f"Missing required template vars: {missing_required}")
    if type_errors:
        raise AppFlowyError(f"Template var type mismatch: {type_errors}")

    return resolved, {
        "required_count": len([name for name, item in spec.items() if item.get("required")]),
        "defaulted_vars": defaulted,
        "provided_var_keys": sorted(provided_vars.keys()),
        "resolved_var_keys": sorted(resolved.keys()),
    }


def _render_string(text: str, values: dict[str, Any], stats: dict[str, Any]) -> Any:
    full_match = FULL_PLACEHOLDER_PATTERN.match(text)
    if full_match:
        key = full_match.group(1)
        value = _get_by_path(values, key)
        stats["placeholder_replacements"] += 1
        stats["placeholder_keys"].add(key)
        return copy.deepcopy(value)

    def replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        value = _get_by_path(values, key)
        stats["placeholder_replacements"] += 1
        stats["placeholder_keys"].add(key)
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if value is None:
            return ""
        return str(value)

    return PLACEHOLDER_PATTERN.sub(replacer, text)


def _render_node(node: Any, values: dict[str, Any], stats: dict[str, Any]) -> Any:
    if isinstance(node, str):
        return _render_string(node, values, stats)
    if isinstance(node, list):
        return [_render_node(item, values, stats) for item in node]
    if isinstance(node, dict):
        return {key: _render_node(value, values, stats) for key, value in node.items()}
    return node


def render_template_with_vars(
    template: dict[str, Any],
    vars_values: dict[str, Any],
    *,
    keep_template_vars: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = copy.deepcopy(template)
    if not keep_template_vars and "template_vars" in source:
        source.pop("template_vars", None)
    stats: dict[str, Any] = {
        "placeholder_replacements": 0,
        "placeholder_keys": set(),
    }
    rendered = _render_node(source, vars_values, stats)
    stats["placeholder_keys"] = sorted(stats["placeholder_keys"])
    return rendered, stats


def write_render_output(payload: dict[str, Any], output_file: str | None) -> str | None:
    if not output_file:
        return None
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    return str(path)
