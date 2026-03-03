from __future__ import annotations

import argparse
from typing import Any

from _common import build_client, load_json_payload, print_json, resolve_token
from appflowy_client import AppFlowyError
from change_report import add_warning, new_change_report, set_after, set_before, set_plan, set_summary
from schema_lib import (
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
    template_payload = None
    if args.target_template or args.target_template_file:
        template_payload = load_json_payload(args.target_template, args.target_template_file)
    if args.target_database_id:
        target_fields = fetch_target_fields_from_database(
            client, token, args.workspace_id, args.target_database_id
        )
        return target_fields, {"type": "database", "database_id": args.target_database_id}
    if template_payload is None:
        raise AppFlowyError(
            "Provide target schema via --target-database-id or --target-template/--target-template-file."
        )
    raw_fields = extract_target_fields(template_payload)
    target_fields = normalize_target_fields(raw_fields)
    return target_fields, {"type": "template", "field_count": len(target_fields)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare current database schema with target schema (template/database)."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--target-database-id", default=None)
    parser.add_argument("--target-template", default=None, help="Target schema/template JSON string.")
    parser.add_argument("--target-template-file", default=None, help="Target schema/template JSON file.")
    parser.add_argument(
        "--include-system-fields",
        action="store_true",
        help="Include system fields (CreatedTime/LastEditedTime...) in diff.",
    )
    parser.add_argument(
        "--rename-hint-threshold",
        type=float,
        default=0.55,
        help="Rename candidate similarity threshold. Default: 0.55",
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

    if not (0.0 <= args.rename_hint_threshold <= 1.0):
        raise AppFlowyError("--rename-hint-threshold must be in [0, 1].")

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

    report = new_change_report(
        action="schema_diff",
        target_type="database",
        target_id=args.database_id,
        dry_run=True,
        input_data={
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "target": target_meta,
            "include_system_fields": bool(args.include_system_fields),
            "rename_hint_threshold": args.rename_hint_threshold,
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
        add_count=len(diff_result.get("add_fields", [])),
        delete_count=len(diff_result.get("delete_fields", [])),
        rename_candidate_count=len(diff_result.get("rename_candidates", [])),
        type_change_count=len(diff_result.get("type_changes", [])),
        select_option_change_count=len(diff_result.get("select_option_changes", [])),
        source_duplicate_names=diff_result.get("source_duplicate_names", []),
        target_duplicate_names=diff_result.get("target_duplicate_names", []),
    )
    if diff_result.get("source_duplicate_names"):
        add_warning(report, "Source schema has duplicate field names; migration may require field_id.")
    if diff_result.get("target_duplicate_names"):
        add_warning(report, "Target schema has duplicate field names; please normalize template fields.")
    set_after(report, applied=False)
    set_summary(
        report,
        changed=any(
            len(diff_result.get(key, [])) > 0
            for key in (
                "add_fields",
                "delete_fields",
                "rename_candidates",
                "type_changes",
                "select_option_changes",
            )
        ),
        add_count=len(diff_result.get("add_fields", [])),
        delete_count=len(diff_result.get("delete_fields", [])),
        rename_candidate_count=len(diff_result.get("rename_candidates", [])),
        type_change_count=len(diff_result.get("type_changes", [])),
        select_option_change_count=len(diff_result.get("select_option_changes", [])),
    )

    print_json(
        {
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "target": target_meta,
            "diff_model": {
                "sections": [
                    "add_fields",
                    "delete_fields",
                    "rename_candidates",
                    "type_changes",
                    "select_option_changes",
                ],
            "note": "rename_candidates are hints; migration execution should re-confirm by field_id.",
        },
        "ignored_system_fields": ignored_system_fields,
        "ignored_target_system_fields": ignored_target_system_fields,
        "diff": diff_result,
        "change_report": report,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
