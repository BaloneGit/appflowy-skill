from __future__ import annotations

import argparse
from typing import Any

import doc_grid_lib as grid_lib
import template_ops_lib as template_ops
from _common import build_client, print_json, resolve_token
from appflowy_client import AppFlowyError
from audit_log import add_error, add_warning, finish_audit_log, new_audit_log, write_audit_log
from change_report import add_item, new_change_report, set_after, set_before, set_plan, set_summary
from template_render_lib import (
    load_template_payload,
    load_vars_payload,
    render_template_with_vars,
    resolve_template_vars,
)

RULE_CLEANUP_DEFAULT_ROWS = "cleanup-default-rows"
RULE_ENSURE_TEMPLATE_FIELDS = "ensure-template-fields"
RULE_REPAIR_SELECT_OPTIONS = "repair-select-options"

ALL_RULES = [
    RULE_CLEANUP_DEFAULT_ROWS,
    RULE_ENSURE_TEMPLATE_FIELDS,
    RULE_REPAIR_SELECT_OPTIONS,
]


def _load_rendered_template(args) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not (args.template or args.template_file):
        return None, None
    template = load_template_payload(args.template, args.template_file)
    provided_vars = load_vars_payload(args.vars, args.vars_file)
    resolved_vars, var_meta = resolve_template_vars(template, provided_vars)
    rendered, render_stats = render_template_with_vars(
        template,
        resolved_vars,
        keep_template_vars=False,
    )
    return rendered, {
        "resolved_vars": resolved_vars,
        "var_meta": var_meta,
        "render_stats": render_stats,
    }


def _resolve_rules(args, template_available: bool) -> list[str]:
    if args.repair:
        return args.repair
    if args.all_template_repairs:
        return list(ALL_RULES)
    if template_available:
        return list(ALL_RULES)
    return [RULE_CLEANUP_DEFAULT_ROWS]


def _plan_cleanup_default_rows(client, token: str, workspace_id: str, database_id: str, max_rows: int) -> dict[str, Any]:
    row_ids = grid_lib.find_empty_row_ids(
        client,
        token,
        workspace_id,
        database_id,
        max_remove=max_rows,
    )
    return {
        "rule": RULE_CLEANUP_DEFAULT_ROWS,
        "planned_row_ids": row_ids,
        "planned_count": len(row_ids),
    }


def _apply_cleanup_default_rows(client, token: str, workspace_id: str, database_id: str, max_rows: int) -> dict[str, Any]:
    removed = grid_lib.cleanup_default_rows(
        client,
        token,
        workspace_id,
        database_id,
        max_remove=max_rows,
        view_ids=None,
    )
    return {
        "rule": RULE_CLEANUP_DEFAULT_ROWS,
        "applied_row_ids": removed,
        "applied_count": len(removed),
    }


def _plan_ensure_template_fields(current_fields: list[dict], template_fields: list[dict]) -> dict[str, Any]:
    pending = template_ops.plan_missing_fields(current_fields, template_fields)
    return {
        "rule": RULE_ENSURE_TEMPLATE_FIELDS,
        "pending_fields": pending,
        "planned_count": len(pending),
    }


def _apply_ensure_template_fields(
    client,
    token: str,
    workspace_id: str,
    database_id: str,
    template_fields: list[dict],
) -> dict[str, Any]:
    created_ids = template_ops.ensure_fields_from_template(
        client,
        token,
        workspace_id,
        database_id,
        template_fields,
    )
    return {
        "rule": RULE_ENSURE_TEMPLATE_FIELDS,
        "created_field_ids": created_ids,
        "applied_count": len(created_ids),
    }


def _plan_repair_select_options(current_fields: list[dict], template_fields: list[dict]) -> dict[str, Any]:
    updates = template_ops.plan_select_option_repairs(current_fields, template_fields)
    return {
        "rule": RULE_REPAIR_SELECT_OPTIONS,
        "pending_updates": updates,
        "planned_count": len(updates),
    }


def _apply_repair_select_options(
    client,
    token: str,
    workspace_id: str,
    database_id: str,
    template_fields: list[dict],
) -> dict[str, Any]:
    updated_field_ids = template_ops.repair_select_options_from_template(
        client,
        token,
        workspace_id,
        database_id,
        template_fields,
    )
    return {
        "rule": RULE_REPAIR_SELECT_OPTIONS,
        "updated_field_ids": updated_field_ids,
        "applied_count": len(updated_field_ids),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run grid repairs with plugin-like rules and unified audit log.")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--template", default=None, help="Template JSON string.")
    parser.add_argument("--template-file", default=None, help="Template JSON file.")
    parser.add_argument("--vars", default=None, help="Template vars JSON string.")
    parser.add_argument("--vars-file", default=None, help="Template vars JSON file.")
    parser.add_argument(
        "--repair",
        action="append",
        choices=ALL_RULES,
        default=[],
        help="Repair rule to run. Can be repeated.",
    )
    parser.add_argument(
        "--all-template-repairs",
        action="store_true",
        help="Run all rules (cleanup-default-rows/ensure-template-fields/repair-select-options).",
    )
    parser.add_argument(
        "--max-default-rows",
        type=int,
        default=3,
        help="Max rows to cleanup for rule cleanup-default-rows. Default: 3",
    )
    parser.add_argument("--execute", action="store_true", help="Apply repairs. Default is dry-run.")
    parser.add_argument("--yes", action="store_true", help="Confirm execution when --execute.")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument(
        "--audit-log-file",
        default=None,
        help="Write execution audit log to file. Default: .tmp/audit_logs/<action>_<time>.json",
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
    if args.max_default_rows < 0:
        raise AppFlowyError("--max-default-rows must be >= 0.")

    audit = new_audit_log(
        action="repair_runner",
        target={"type": "database", "id": args.database_id},
        input_data={
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "template_file": args.template_file,
            "repairs": args.repair,
            "all_template_repairs": bool(args.all_template_repairs),
            "execute": bool(args.execute),
            "confirmed": bool(args.yes),
            "continue_on_error": bool(args.continue_on_error),
        },
    )
    report = new_change_report(
        action="repair_runner",
        target_type="database",
        target_id=args.database_id,
        dry_run=dry_run,
        input_data={
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "execute": bool(args.execute),
            "confirmed": bool(args.yes),
            "max_default_rows": args.max_default_rows,
        },
    )

    try:
        client = build_client(args)
        token = resolve_token(args, client)

        rendered_template, render_meta = _load_rendered_template(args)
        template_fields = (rendered_template or {}).get("fields") or []
        rules = _resolve_rules(args, template_available=bool(rendered_template))
        if not rules:
            raise AppFlowyError("No repair rule selected.")
        if (
            rendered_template is None
            and any(rule in {RULE_ENSURE_TEMPLATE_FIELDS, RULE_REPAIR_SELECT_OPTIONS} for rule in rules)
        ):
            raise AppFlowyError(
                "Rules ensure-template-fields/repair-select-options require --template or --template-file."
            )

        fields_resp = grid_lib.get_database_fields(client, token, args.workspace_id, args.database_id)
        current_fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []
        row_ids_before = grid_lib.list_row_ids(client, token, args.workspace_id, args.database_id)

        set_before(
            report,
            field_count_before=len(current_fields),
            row_count_before=len(row_ids_before),
        )
        set_plan(
            report,
            selected_rules=rules,
            dry_run=dry_run,
        )

        results: list[dict[str, Any]] = []
        failed_count = 0
        for rule in rules:
            try:
                if rule == RULE_CLEANUP_DEFAULT_ROWS:
                    planned = _plan_cleanup_default_rows(
                        client,
                        token,
                        args.workspace_id,
                        args.database_id,
                        args.max_default_rows,
                    )
                    if dry_run:
                        item = {
                            "rule": rule,
                            "status": "planned",
                            "detail": planned,
                        }
                    else:
                        applied = _apply_cleanup_default_rows(
                            client,
                            token,
                            args.workspace_id,
                            args.database_id,
                            args.max_default_rows,
                        )
                        item = {
                            "rule": rule,
                            "status": "executed",
                            "detail": {
                                "planned": planned,
                                "applied": applied,
                            },
                        }
                elif rule == RULE_ENSURE_TEMPLATE_FIELDS:
                    planned = _plan_ensure_template_fields(current_fields, template_fields)
                    if dry_run:
                        item = {"rule": rule, "status": "planned", "detail": planned}
                    else:
                        applied = _apply_ensure_template_fields(
                            client,
                            token,
                            args.workspace_id,
                            args.database_id,
                            template_fields,
                        )
                        item = {
                            "rule": rule,
                            "status": "executed",
                            "detail": {
                                "planned": planned,
                                "applied": applied,
                            },
                        }
                elif rule == RULE_REPAIR_SELECT_OPTIONS:
                    planned = _plan_repair_select_options(current_fields, template_fields)
                    if dry_run:
                        item = {"rule": rule, "status": "planned", "detail": planned}
                    else:
                        applied = _apply_repair_select_options(
                            client,
                            token,
                            args.workspace_id,
                            args.database_id,
                            template_fields,
                        )
                        item = {
                            "rule": rule,
                            "status": "executed",
                            "detail": {
                                "planned": planned,
                                "applied": applied,
                            },
                        }
                else:
                    raise AppFlowyError(f"Unsupported rule: {rule}")
                results.append(item)
                add_item(report, item)
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                failed_item = {
                    "rule": rule,
                    "status": "failed",
                    "error": str(exc),
                }
                results.append(failed_item)
                add_item(report, failed_item)
                add_error(audit, f"{rule} failed: {exc}")
                if not args.continue_on_error:
                    break

        fields_after_resp = grid_lib.get_database_fields(client, token, args.workspace_id, args.database_id)
        fields_after = fields_after_resp.get("data", []) if isinstance(fields_after_resp, dict) else []
        row_ids_after = grid_lib.list_row_ids(client, token, args.workspace_id, args.database_id)
        set_after(
            report,
            applied=not dry_run and failed_count == 0,
            field_count_after=len(fields_after),
            row_count_after=len(row_ids_after),
            field_count_diff=len(fields_after) - len(current_fields),
            row_count_diff=len(row_ids_after) - len(row_ids_before),
        )
        set_summary(
            report,
            rule_count=len(rules),
            planned_count=len([item for item in results if item.get("status") == "planned"]),
            executed_count=len([item for item in results if item.get("status") == "executed"]),
            failed_count=failed_count,
            dry_run=dry_run,
        )

        if not rendered_template and any(
            rule in {RULE_ENSURE_TEMPLATE_FIELDS, RULE_REPAIR_SELECT_OPTIONS} for rule in rules
        ):
            add_warning(audit, "Template-specific rules selected without rendered template.")

        finish_audit_log(
            audit,
            status="success" if failed_count == 0 else "partial_failed",
            result={
                "rule_count": len(rules),
                "dry_run": dry_run,
                "failed_count": failed_count,
            },
        )
        audit_path = write_audit_log(audit, args.audit_log_file)
        print_json(
            {
                "workspace_id": args.workspace_id,
                "database_id": args.database_id,
                "dry_run": dry_run,
                "selected_rules": rules,
                "render_meta": render_meta,
                "results": results,
                "audit_log_file": audit_path,
                "change_report": report,
            }
        )
        return 0 if failed_count == 0 else 2
    except Exception as exc:  # noqa: BLE001
        add_error(audit, str(exc))
        finish_audit_log(audit, status="failed", result={"error": str(exc)})
        audit_path = write_audit_log(audit, args.audit_log_file)
        raise AppFlowyError(f"repair-runner failed. audit_log_file={audit_path}. error={exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
