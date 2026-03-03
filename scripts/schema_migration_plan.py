from __future__ import annotations

import argparse
from typing import Any

from _common import build_client, load_json_payload, print_json, resolve_token
from appflowy_client import AppFlowyError
from change_report import add_warning, new_change_report, set_after, set_before, set_plan, set_summary
from schema_lib import (
    build_migration_plan,
    build_schema_diff,
    extract_target_fields,
    fetch_current_fields,
    fetch_target_fields_from_database,
    normalize_target_fields,
)

SYSTEM_FIELD_TYPES = {"CreatedTime", "LastEditedTime", "CreatedBy", "LastEditedBy"}


def _filter_source_fields(fields, include_system_fields: bool):
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


def _filter_target_fields(fields, include_system_fields: bool):
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


def _risk_summary(operations: list[dict[str, Any]]) -> dict[str, int]:
    risk_counts = {"low": 0, "medium": 0, "high": 0}
    for op in operations:
        risk = str(op.get("risk", "")).lower()
        if risk in risk_counts:
            risk_counts[risk] += 1
    return risk_counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate schema migration plan from current schema to target schema."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--target-database-id", default=None)
    parser.add_argument("--target-template", default=None, help="Target schema/template JSON string.")
    parser.add_argument("--target-template-file", default=None, help="Target schema/template JSON file.")
    parser.add_argument(
        "--include-system-fields",
        action="store_true",
        help="Include system fields (CreatedTime/LastEditedTime...) in plan.",
    )
    parser.add_argument(
        "--rename-hint-threshold",
        type=float,
        default=0.55,
        help="Rename candidate similarity threshold. Default: 0.55",
    )
    parser.add_argument(
        "--rename-apply-threshold",
        type=float,
        default=0.75,
        help="Rename auto-apply threshold in plan. Default: 0.75",
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

    for name, value in (
        ("--rename-hint-threshold", args.rename_hint_threshold),
        ("--rename-apply-threshold", args.rename_apply_threshold),
    ):
        if not (0.0 <= value <= 1.0):
            raise AppFlowyError(f"{name} must be in [0, 1].")

    client = build_client(args)
    token = resolve_token(args, client)

    source_fields_raw = fetch_current_fields(client, token, args.workspace_id, args.database_id)
    source_fields, ignored_system_fields = _filter_source_fields(
        source_fields_raw, bool(args.include_system_fields)
    )
    target_fields_raw, target_meta = _resolve_target_fields(args, client, token)
    target_fields, ignored_target_system_fields = _filter_target_fields(
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

    operations = plan.get("operations", [])
    risk_counts = _risk_summary(operations)
    blocked_ops = plan.get("blocked_operations", [])

    report = new_change_report(
        action="schema_migration_plan",
        target_type="database",
        target_id=args.database_id,
        dry_run=True,
        input_data={
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "target": target_meta,
            "rename_hint_threshold": args.rename_hint_threshold,
            "rename_apply_threshold": args.rename_apply_threshold,
            "include_system_fields": bool(args.include_system_fields),
        },
    )
    set_before(
        report,
        source_field_count=len(source_fields),
        target_field_count=len(target_fields),
        ignored_system_field_count=len(ignored_system_fields),
        ignored_target_system_field_count=len(ignored_target_system_fields),
    )
    set_plan(
        report,
        operation_count=len(operations),
        blocked_operation_count=len(blocked_ops),
        op_counts=plan.get("op_counts", {}),
        risk_counts=risk_counts,
        requires_manual_review=bool(plan.get("requires_manual_review")),
        requires_confirmation=bool(plan.get("requires_confirmation")),
    )
    if blocked_ops:
        add_warning(report, "Plan contains blocked operations. Migration execute must skip or require manual handling.")
    if diff_result.get("source_duplicate_names"):
        add_warning(report, "Source schema has duplicate field names; migration should prefer field_id.")
    if diff_result.get("target_duplicate_names"):
        add_warning(report, "Target schema has duplicate field names; clean template before execute.")
    set_after(report, applied=False)
    set_summary(
        report,
        operation_count=len(operations),
        blocked_operation_count=len(blocked_ops),
        low_risk_count=risk_counts.get("low", 0),
        medium_risk_count=risk_counts.get("medium", 0),
        high_risk_count=risk_counts.get("high", 0),
        requires_manual_review=bool(plan.get("requires_manual_review")),
    )

    print_json(
        {
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "target": target_meta,
            "ignored_system_fields": ignored_system_fields,
            "ignored_target_system_fields": ignored_target_system_fields,
            "diff": diff_result,
            "plan": {
                "operations": operations,
                "op_counts": plan.get("op_counts", {}),
                "risk_counts": risk_counts,
                "blocked_operations": blocked_ops,
                "requires_manual_review": bool(plan.get("requires_manual_review")),
                "requires_confirmation": bool(plan.get("requires_confirmation")),
                "execute_guardrail": {
                    "default_mode": "dry-run",
                    "recommended_flags": ["--execute", "--yes"],
                },
            },
            "change_report": report,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
