from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import doc_grid_lib as grid_lib
from _common import build_client, load_json_payload, print_json, resolve_token
from appflowy_client import AppFlowyError
from change_report import add_error, add_item, add_warning, new_change_report, set_after, set_before, set_plan, set_summary
from schema_lib import (
    FIELD_TYPE_NAME_TO_INT,
    build_migration_plan,
    build_schema_diff,
    extract_target_fields,
    fetch_current_fields,
    fetch_target_fields_from_database,
    normalize_field_type,
    normalize_target_fields,
)

SYSTEM_FIELD_TYPES = {"CreatedTime", "LastEditedTime", "CreatedBy", "LastEditedBy"}
HIGH_RISK_OPS = {"delete_field", "change_field_type"}
DESTRUCTIVE_OPS = {"delete_field", "change_field_type"}


def _filter_fields(fields, include_system_fields: bool):
    if include_system_fields:
        return fields, []
    kept = []
    ignored = []
    for field in fields:
        if field.field_type in SYSTEM_FIELD_TYPES:
            ignored.append(field.to_public())
            continue
        kept.append(field)
    return kept, ignored


def _resolve_target_fields(args, client, token: str):
    target_payload = None
    if args.target_template or args.target_template_file:
        target_payload = load_json_payload(args.target_template, args.target_template_file)
    if args.target_database_id:
        target_fields = fetch_target_fields_from_database(
            client, token, args.workspace_id, args.target_database_id
        )
        return target_fields, {"type": "database", "database_id": args.target_database_id}
    if target_payload is None:
        raise AppFlowyError(
            "Provide target schema via --target-database-id or --target-template/--target-template-file."
        )
    fields = extract_target_fields(target_payload)
    target_fields = normalize_target_fields(fields)
    return target_fields, {"type": "template", "field_count": len(target_fields)}


def _op_signature(op: dict[str, Any]) -> str:
    return json.dumps(op, sort_keys=True, ensure_ascii=True)


def _type_to_select_key(field_type: Any) -> str | None:
    normalized = normalize_field_type(field_type)
    if normalized == "SingleSelect":
        return "3"
    if normalized == "MultiSelect":
        return "4"
    return None


def _slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return value or "option"


def _details_from_names(names: list[str]) -> list[dict[str, Any]]:
    result = []
    for idx, name in enumerate(names):
        label = str(name or "").strip()
        if not label:
            continue
        result.append(
            {
                "id": f"auto-{_slug(label)}-{idx + 1}",
                "name": label,
                "color": "Purple",
            }
        )
    return result


def _normalize_field_type_for_api(value: Any) -> Any:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
        normalized = normalize_field_type(text)
        if normalized in FIELD_TYPE_NAME_TO_INT:
            return FIELD_TYPE_NAME_TO_INT[normalized]
        return text
    return value


def _load_plan_file(plan_file: str) -> dict[str, Any]:
    raw = Path(plan_file).read_bytes()
    errors: list[str] = []
    payload = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            payload = json.loads(raw.decode(encoding))
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{encoding}: {exc}")
    if payload is None:
        raise AppFlowyError(
            "Failed to parse plan file as JSON. "
            "Supported encodings: UTF-8/UTF-8 BOM/UTF-16. "
            f"Details: {' | '.join(errors)}"
        )
    if not isinstance(payload, dict):
        raise AppFlowyError("--plan-file payload must be a JSON object.")
    if isinstance(payload.get("plan"), dict) and isinstance(payload["plan"].get("operations"), list):
        return {
            "raw": payload,
            "plan": payload["plan"],
            "diff": payload.get("diff"),
            "target_meta": payload.get("target"),
            "target_schema": payload.get("target_schema"),
        }
    if isinstance(payload.get("operations"), list):
        return {
            "raw": payload,
            "plan": payload,
            "diff": payload.get("diff"),
            "target_meta": payload.get("target"),
            "target_schema": payload.get("target_schema"),
        }
    raise AppFlowyError("--plan-file must include 'plan.operations' or top-level 'operations'.")


def _build_add_field_payload(op: dict[str, Any]) -> dict[str, Any]:
    field_data = op.get("field_data") if isinstance(op.get("field_data"), dict) else {}
    target_payload = field_data.get("target_payload") if isinstance(field_data.get("target_payload"), dict) else {}
    payload = dict(target_payload)
    payload["name"] = payload.get("name") or op.get("field_name")
    field_type_raw = payload.get("field_type", field_data.get("field_type_raw", op.get("field_type")))
    payload["field_type"] = _normalize_field_type_for_api(field_type_raw)

    field_type_name = normalize_field_type(payload.get("field_type"))
    if field_type_name in {"SingleSelect", "MultiSelect"} and "type_option_data" not in payload:
        details = field_data.get("select_options_detail")
        if not isinstance(details, list) or not details:
            details = _details_from_names(field_data.get("select_options") or [])
        if details:
            payload["type_option_data"] = {
                "content": json.dumps(
                    {
                        "options": details,
                        "disable_color": bool(field_data.get("select_disable_color", False)),
                    },
                    ensure_ascii=False,
                )
            }

    if field_type_name == "Relation":
        type_option_data = payload.get("type_option_data")
        if isinstance(type_option_data, dict):
            db_id = str(type_option_data.get("database_id") or "")
            if not db_id or db_id == "<db_id_placeholder>":
                raise AppFlowyError(
                    f"Relation field '{payload.get('name')}' has unresolved database_id. Manual migration required."
                )
        else:
            raise AppFlowyError(
                f"Relation field '{payload.get('name')}' missing type_option_data.database_id."
            )
    return payload


def _resolve_field_by_id_or_name(fields: list[dict], field_id: str | None, field_name: str | None) -> dict[str, Any]:
    by_id = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        current_id = field.get("id")
        current_name = field.get("name")
        if current_id:
            by_id[str(current_id)] = field
        if current_name:
            by_name.setdefault(str(current_name), []).append(field)
    if field_id:
        found = by_id.get(str(field_id))
        if found:
            return found
        raise AppFlowyError(f"Field id not found: {field_id}")
    if not field_name:
        raise AppFlowyError("Field identifier missing.")
    matches = by_name.get(str(field_name), [])
    if not matches:
        raise AppFlowyError(f"Field name not found: {field_name}")
    if len(matches) > 1:
        ids = [item.get("id") for item in matches]
        raise AppFlowyError(f"Field name is ambiguous: {field_name}. Matched ids: {ids}")
    return matches[0]


def _execute_add_field(client, token: str, workspace_id: str, database_id: str, op: dict[str, Any]) -> dict[str, Any]:
    payload = _build_add_field_payload(op)
    created_field_id = grid_lib.add_database_field(client, token, workspace_id, database_id, payload)
    return {
        "op": "add_field",
        "status": "executed",
        "field_name": payload.get("name"),
        "created_field_id": created_field_id,
        "payload": payload,
    }


def _execute_rename_field(client, token: str, workspace_id: str, database_id: str, op: dict[str, Any]) -> dict[str, Any]:
    field_id = op.get("field_id")
    new_name = op.get("to_name")
    if not field_id or not new_name:
        raise AppFlowyError("rename_field requires field_id and to_name.")
    doc_state, state_vector = grid_lib.fetch_collab_state(
        client, token, workspace_id, database_id, grid_lib.DB_COLLAB_TYPE
    )
    update = grid_lib.run_node_rename_fields(
        doc_state,
        state_vector,
        [{"field_id": field_id, "new_name": new_name}],
    )
    grid_lib.post_web_update(
        client,
        token,
        workspace_id,
        database_id,
        grid_lib.DB_COLLAB_TYPE,
        update,
    )
    return {
        "op": "rename_field",
        "status": "executed",
        "field_id": field_id,
        "from_name": op.get("from_name"),
        "to_name": new_name,
    }


def _execute_delete_field(client, token: str, workspace_id: str, database_id: str, op: dict[str, Any]) -> dict[str, Any]:
    field_id = op.get("field_id")
    if not field_id:
        raise AppFlowyError("delete_field requires field_id.")
    fields_resp = grid_lib.get_database_fields(client, token, workspace_id, database_id)
    fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []
    target = _resolve_field_by_id_or_name(fields, str(field_id), op.get("field_name"))
    if bool(target.get("is_primary")):
        raise AppFlowyError(f"Primary field deletion is blocked: {target.get('name')}")

    doc_state, state_vector = grid_lib.fetch_collab_state(
        client, token, workspace_id, database_id, grid_lib.DB_COLLAB_TYPE
    )
    update = grid_lib.run_node_delete_fields(doc_state, state_vector, [str(field_id)])
    grid_lib.post_web_update(
        client,
        token,
        workspace_id,
        database_id,
        grid_lib.DB_COLLAB_TYPE,
        update,
    )
    return {
        "op": "delete_field",
        "status": "executed",
        "field_id": field_id,
        "field_name": target.get("name"),
    }


def _execute_update_select_options(
    client, token: str, workspace_id: str, database_id: str, op: dict[str, Any]
) -> dict[str, Any]:
    fields_resp = grid_lib.get_database_fields(client, token, workspace_id, database_id)
    fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []
    target = _resolve_field_by_id_or_name(fields, op.get("field_id"), op.get("field_name"))
    field_id = target.get("id")
    field_type = normalize_field_type(target.get("field_type"))
    type_key = _type_to_select_key(field_type)
    if not type_key:
        raise AppFlowyError(f"Field is not select type: {target.get('name')}")

    details = op.get("to_options_detail")
    if not isinstance(details, list) or not details:
        details = _details_from_names(op.get("to_options") or [])
    if not details:
        raise AppFlowyError(f"Target options missing for field: {target.get('name')}")
    content = json.dumps(
        {
            "options": details,
            "disable_color": bool(op.get("to_disable_color", False)),
        },
        ensure_ascii=False,
    )
    doc_state, state_vector = grid_lib.fetch_collab_state(
        client, token, workspace_id, database_id, grid_lib.DB_COLLAB_TYPE
    )
    update = grid_lib.run_node_update_select_options(
        doc_state,
        state_vector,
        [{"field_id": field_id, "type_key": type_key, "content": content}],
    )
    grid_lib.post_web_update(
        client,
        token,
        workspace_id,
        database_id,
        grid_lib.DB_COLLAB_TYPE,
        update,
    )
    return {
        "op": "update_select_options",
        "status": "executed",
        "field_id": field_id,
        "field_name": target.get("name"),
        "option_count": len(details),
    }


def _summarize_diff(diff_result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(diff_result, dict):
        return {"available": False}
    return {
        "available": True,
        "add_count": len(diff_result.get("add_fields", [])),
        "delete_count": len(diff_result.get("delete_fields", [])),
        "rename_candidate_count": len(diff_result.get("rename_candidates", [])),
        "type_change_count": len(diff_result.get("type_changes", [])),
        "select_option_change_count": len(diff_result.get("select_option_changes", [])),
    }


def _collect_ops(plan: dict[str, Any]) -> list[dict[str, Any]]:
    operations = plan.get("operations")
    if not isinstance(operations, list):
        raise AppFlowyError("Invalid plan: operations must be an array.")
    return [item for item in operations if isinstance(item, dict)]


def _validate_execution_guardrails(
    execute: bool,
    executable_ops: list[dict[str, Any]],
    *,
    allow_high_risk: bool,
    allow_delete_fields: bool,
) -> None:
    if not execute:
        return
    high_risk_ops = [op for op in executable_ops if op.get("op") in HIGH_RISK_OPS or op.get("risk") == "high"]
    delete_ops = [op for op in executable_ops if op.get("op") == "delete_field"]
    if high_risk_ops and not allow_high_risk:
        raise AppFlowyError(
            "High risk operations detected. Re-run with --allow-high-risk to continue execution."
        )
    if delete_ops and not allow_delete_fields:
        raise AppFlowyError(
            "Delete field operations detected. Re-run with --allow-delete-fields to continue execution."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply schema migration plan. Default mode is dry-run. "
            "Supports plan file input or generated plan from target schema."
        )
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--plan-file", default=None, help="Path to migration plan JSON.")
    parser.add_argument("--target-database-id", default=None)
    parser.add_argument("--target-template", default=None, help="Target schema/template JSON string.")
    parser.add_argument("--target-template-file", default=None, help="Target schema/template JSON file.")
    parser.add_argument(
        "--include-system-fields",
        action="store_true",
        help="Include system fields (CreatedTime/LastEditedTime...) in diff/plan.",
    )
    parser.add_argument(
        "--rename-hint-threshold",
        type=float,
        default=0.55,
        help="Rename candidate similarity threshold when generating plan. Default: 0.55",
    )
    parser.add_argument(
        "--rename-apply-threshold",
        type=float,
        default=0.75,
        help="Rename auto-apply threshold when generating plan. Default: 0.75",
    )
    parser.add_argument("--execute", action="store_true", help="Apply migration. Default mode is dry-run.")
    parser.add_argument("--yes", action="store_true", help="Confirm execution when --execute.")
    parser.add_argument(
        "--allow-high-risk",
        action="store_true",
        help="Allow execution of high risk operations (delete/type change).",
    )
    parser.add_argument(
        "--allow-delete-fields",
        action="store_true",
        help="Allow execution of delete_field operations.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue executing following operations when one operation fails.",
    )
    parser.add_argument("--token", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--config", default=None, help="Path to config JSON (optional).")
    parser.add_argument("--env", default=None, help="Path to .env file (optional, opt-in).")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--gotrue-url", default=None)
    parser.add_argument("--client-version", default=None)
    parser.add_argument("--device-id", default=None)
    args = parser.parse_args()

    dry_run = not args.execute
    if args.execute and not args.yes:
        raise AppFlowyError("Execution requires --yes when --execute is set.")

    if not (0.0 <= args.rename_hint_threshold <= 1.0):
        raise AppFlowyError("--rename-hint-threshold must be in [0, 1].")
    if not (0.0 <= args.rename_apply_threshold <= 1.0):
        raise AppFlowyError("--rename-apply-threshold must be in [0, 1].")

    if not args.plan_file and not (
        args.target_database_id or args.target_template or args.target_template_file
    ):
        raise AppFlowyError(
            "Provide --plan-file, or provide target schema via --target-database-id/"
            "--target-template/--target-template-file."
        )

    client = build_client(args)
    token = resolve_token(args, client)

    source_fields_raw = fetch_current_fields(client, token, args.workspace_id, args.database_id)
    source_fields, ignored_system_fields = _filter_fields(
        source_fields_raw, bool(args.include_system_fields)
    )

    plan_source_type = "generated"
    target_meta: dict[str, Any] = {"type": "unknown"}
    diff_result: dict[str, Any] | None = None
    plan: dict[str, Any]
    target_fields = None

    if args.plan_file:
        plan_source_type = "file"
        loaded = _load_plan_file(args.plan_file)
        plan = loaded["plan"]
        diff_result = loaded.get("diff")
        target_meta = loaded.get("target_meta") or {"type": "unknown"}
        target_schema = loaded.get("target_schema")
        if isinstance(target_schema, dict) and isinstance(target_schema.get("fields"), list):
            target_fields = normalize_target_fields(target_schema.get("fields") or [])
    else:
        target_fields_raw, target_meta = _resolve_target_fields(args, client, token)
        target_fields, _ignored_target = _filter_fields(
            target_fields_raw, bool(args.include_system_fields)
        )
        diff_result = build_schema_diff(
            source_fields,
            target_fields,
            rename_hint_threshold=args.rename_hint_threshold,
        )
        plan = build_migration_plan(
            diff_result,
            rename_apply_threshold=args.rename_apply_threshold,
        )

    operations = _collect_ops(plan)
    blocked_ops = plan.get("blocked_operations", [])
    blocked_signatures = {
        _op_signature(item)
        for item in blocked_ops
        if isinstance(item, dict)
    }

    executable_ops: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    for op in operations:
        op_name = str(op.get("op") or "unknown")
        if _op_signature(op) in blocked_signatures:
            skipped_items.append(
                {
                    "op": op_name,
                    "status": "skipped",
                    "reason": "blocked_by_plan",
                    "detail": op,
                }
            )
            continue
        if not bool(op.get("auto_executable", False)):
            skipped_items.append(
                {
                    "op": op_name,
                    "status": "skipped",
                    "reason": "manual_review_required",
                    "detail": op,
                }
            )
            continue
        if op_name == "change_field_type":
            skipped_items.append(
                {
                    "op": op_name,
                    "status": "skipped",
                    "reason": "type_change_not_supported_in_v0_3_m2",
                    "detail": op,
                }
            )
            continue
        executable_ops.append(op)

    _validate_execution_guardrails(
        args.execute,
        executable_ops,
        allow_high_risk=bool(args.allow_high_risk),
        allow_delete_fields=bool(args.allow_delete_fields),
    )

    before_diff = None
    if target_fields is not None:
        before_diff = build_schema_diff(
            source_fields,
            target_fields,
            rename_hint_threshold=args.rename_hint_threshold,
        )
    elif diff_result is not None:
        before_diff = diff_result

    report = new_change_report(
        action="apply_schema_migration",
        target_type="database",
        target_id=args.database_id,
        dry_run=dry_run,
        input_data={
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "plan_source": plan_source_type,
            "plan_file": args.plan_file,
            "target": target_meta,
            "execute": bool(args.execute),
            "confirmed": bool(args.yes),
            "allow_high_risk": bool(args.allow_high_risk),
            "allow_delete_fields": bool(args.allow_delete_fields),
            "continue_on_error": bool(args.continue_on_error),
        },
    )
    add_warning(
        report,
        "Rollback is not automatic in v0.3 M2. Capture collab snapshot before production execution.",
    )

    set_before(
        report,
        field_count_before=len(source_fields),
        ignored_system_field_count=len(ignored_system_fields),
        before_diff_summary=_summarize_diff(before_diff),
    )
    set_plan(
        report,
        operation_count=len(operations),
        executable_count=len(executable_ops),
        blocked_count=len([item for item in skipped_items if item.get("reason") == "blocked_by_plan"]),
        manual_review_count=len(
            [item for item in skipped_items if item.get("reason") == "manual_review_required"]
        ),
        plan_source=plan_source_type,
    )

    execution_results = list(skipped_items)
    failed_count = 0
    executed_count = 0
    if args.execute:
        for op in executable_ops:
            op_name = str(op.get("op") or "unknown")
            try:
                if op_name == "add_field":
                    result = _execute_add_field(
                        client, token, args.workspace_id, args.database_id, op
                    )
                elif op_name == "rename_field":
                    result = _execute_rename_field(
                        client, token, args.workspace_id, args.database_id, op
                    )
                elif op_name == "delete_field":
                    result = _execute_delete_field(
                        client, token, args.workspace_id, args.database_id, op
                    )
                elif op_name == "update_select_options":
                    result = _execute_update_select_options(
                        client, token, args.workspace_id, args.database_id, op
                    )
                else:
                    result = {
                        "op": op_name,
                        "status": "skipped",
                        "reason": "unsupported_operation",
                        "detail": op,
                    }
                execution_results.append(result)
                if result.get("status") == "executed":
                    executed_count += 1
                elif result.get("status") == "failed":
                    failed_count += 1
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                failed = {
                    "op": op_name,
                    "status": "failed",
                    "error": str(exc),
                    "detail": op,
                }
                execution_results.append(failed)
                add_error(report, f"{op_name} failed: {exc}")
                if not args.continue_on_error:
                    break
    else:
        for op in executable_ops:
            execution_results.append(
                {
                    "op": op.get("op"),
                    "status": "planned",
                    "detail": op,
                }
            )

    for item in execution_results:
        add_item(report, item)

    latest_fields_raw = fetch_current_fields(client, token, args.workspace_id, args.database_id)
    latest_fields, ignored_after_system_fields = _filter_fields(
        latest_fields_raw, bool(args.include_system_fields)
    )
    after_diff = None
    if target_fields is not None:
        after_diff = build_schema_diff(
            latest_fields,
            target_fields,
            rename_hint_threshold=args.rename_hint_threshold,
        )
    else:
        add_warning(
            report,
            "target_schema missing in plan file; after diff is unavailable.",
        )

    set_after(
        report,
        applied=bool(args.execute and executed_count > 0 and failed_count == 0),
        field_count_after=len(latest_fields),
        ignored_after_system_field_count=len(ignored_after_system_fields),
        after_diff_summary=_summarize_diff(after_diff),
    )
    set_summary(
        report,
        operation_count=len(operations),
        executable_count=len(executable_ops),
        executed_count=executed_count,
        skipped_count=len([item for item in execution_results if item.get("status") == "skipped"]),
        planned_count=len([item for item in execution_results if item.get("status") == "planned"]),
        failed_count=failed_count,
        dry_run=dry_run,
    )

    output = {
        "workspace_id": args.workspace_id,
        "database_id": args.database_id,
        "dry_run": dry_run,
        "plan_source": plan_source_type,
        "target": target_meta,
        "ignored_system_fields": ignored_system_fields,
        "operations": {
            "total": len(operations),
            "executable": len(executable_ops),
            "results": execution_results,
        },
        "guardrails": {
            "default_mode": "dry-run",
            "execute_requires_yes": True,
            "high_risk_requires_allow_high_risk": True,
            "delete_field_requires_allow_delete_fields": True,
            "rollback_hint": "Capture collab snapshot before execute in production.",
        },
        "before_diff": before_diff,
        "after_diff": after_diff,
        "change_report": report,
    }
    print_json(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
