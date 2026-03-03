from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import doc_grid_lib as grid_lib
from _common import build_client, print_json, resolve_token
from apply_schema_migration import (
    _execute_add_field,
    _execute_delete_field,
    _execute_rename_field,
    _execute_update_select_options,
)
from appflowy_client import AppFlowyError
from audit_log import add_error, add_warning, finish_audit_log, new_audit_log, write_audit_log
from change_report import add_warning as add_report_warning
from change_report import new_change_report, set_after, set_before, set_plan, set_summary
from schema_lib import (
    build_migration_plan,
    build_schema_diff,
    normalize_current_field,
    normalize_target_field_from_database,
)

COLLAB_KIND_TO_TYPE = {"doc": grid_lib.DOC_COLLAB_TYPE, "database": grid_lib.DB_COLLAB_TYPE}
COLLAB_TYPE_TO_KIND = {value: key for key, value in COLLAB_KIND_TO_TYPE.items()}


def _bytes_from_int_list(values: list[int]) -> bytes:
    data = bytearray()
    for item in values:
        if not isinstance(item, int):
            raise AppFlowyError(f"Snapshot bytes must be int list. got: {type(item)}")
        if item < 0 or item > 255:
            raise AppFlowyError(f"Snapshot byte out of range [0,255]: {item}")
        data.append(item)
    return bytes(data)


def _sha256_of_int_list(values: list[int]) -> str:
    return hashlib.sha256(_bytes_from_int_list(values)).hexdigest()


def _load_json_with_fallback(path: str) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            payload = json.loads(raw.decode(encoding))
            if isinstance(payload, dict):
                return payload
            raise AppFlowyError("Snapshot payload must be a JSON object.")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{encoding}: {exc}")
    raise AppFlowyError(
        "Failed to parse snapshot file. Supported encodings: UTF-8/UTF-8 BOM/UTF-16. "
        f"Details: {' | '.join(errors)}"
    )


def _resolve_override_target(args) -> tuple[str | None, int | None, str | None]:
    provided = [bool(args.database_id), bool(args.view_id), bool(args.object_id)]
    if sum(provided) == 0:
        return None, None, None
    if sum(provided) > 1:
        raise AppFlowyError("Provide at most one override target: --database-id or --view-id or --object-id.")
    if args.database_id:
        return args.database_id, grid_lib.DB_COLLAB_TYPE, "database"
    if args.view_id:
        return args.view_id, grid_lib.DOC_COLLAB_TYPE, "doc"
    if not args.collab_kind:
        raise AppFlowyError("--object-id requires --collab-kind when used as override target.")
    collab_type = COLLAB_KIND_TO_TYPE[args.collab_kind]
    return args.object_id, collab_type, args.collab_kind


def _resolve_target(args, snapshot: dict[str, Any]) -> tuple[str, str, int, str]:
    snap_workspace = str(snapshot.get("workspace_id") or "")
    snap_object_id = str(snapshot.get("object_id") or "")
    snap_collab_type = snapshot.get("collab_type")
    if not snap_workspace or not snap_object_id or not isinstance(snap_collab_type, int):
        raise AppFlowyError("Snapshot file missing workspace_id/object_id/collab_type.")
    snap_kind = str(snapshot.get("collab_kind") or COLLAB_TYPE_TO_KIND.get(snap_collab_type) or "unknown")

    workspace_id = args.workspace_id or snap_workspace
    object_id, collab_type, collab_kind = _resolve_override_target(args)
    object_id = object_id or snap_object_id
    collab_type = collab_type if collab_type is not None else snap_collab_type
    collab_kind = collab_kind or snap_kind

    if not args.allow_target_mismatch:
        mismatches = []
        if workspace_id != snap_workspace:
            mismatches.append(f"workspace_id: snapshot={snap_workspace}, input={workspace_id}")
        if object_id != snap_object_id:
            mismatches.append(f"object_id: snapshot={snap_object_id}, input={object_id}")
        if collab_type != snap_collab_type:
            mismatches.append(f"collab_type: snapshot={snap_collab_type}, input={collab_type}")
        if mismatches:
            raise AppFlowyError(
                "Snapshot target mismatch. Use --allow-target-mismatch to override. "
                f"Details: {mismatches}"
            )

    return workspace_id, object_id, int(collab_type), collab_kind


def _schema_summary(diff_result: dict[str, Any]) -> dict[str, int]:
    return {
        "add_count": len(diff_result.get("add_fields", [])),
        "delete_count": len(diff_result.get("delete_fields", [])),
        "rename_candidate_count": len(diff_result.get("rename_candidates", [])),
        "type_change_count": len(diff_result.get("type_changes", [])),
        "select_option_change_count": len(diff_result.get("select_option_changes", [])),
    }


def _execute_schema_rollback(
    client,
    token: str,
    workspace_id: str,
    database_id: str,
    snapshot_fields: list[dict[str, Any]],
    *,
    execute: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    current_resp = grid_lib.get_database_fields(client, token, workspace_id, database_id)
    current_fields_raw = current_resp.get("data", []) if isinstance(current_resp, dict) else []
    source_fields = [normalize_current_field(item) for item in current_fields_raw if isinstance(item, dict)]
    target_fields = [normalize_target_field_from_database(item) for item in snapshot_fields if isinstance(item, dict)]
    before_diff = build_schema_diff(source_fields, target_fields, rename_hint_threshold=1.0)
    plan = build_migration_plan(before_diff, rename_apply_threshold=0.75)

    blocked_signatures = {
        json.dumps(item, sort_keys=True, ensure_ascii=True)
        for item in plan.get("blocked_operations", [])
        if isinstance(item, dict)
    }
    results: list[dict[str, Any]] = []
    for op in plan.get("operations", []) or []:
        if not isinstance(op, dict):
            continue
        signature = json.dumps(op, sort_keys=True, ensure_ascii=True)
        op_name = str(op.get("op") or "unknown")
        if signature in blocked_signatures:
            results.append(
                {
                    "op": op_name,
                    "status": "skipped",
                    "reason": "blocked_by_plan",
                    "detail": op,
                }
            )
            continue
        if not bool(op.get("auto_executable", False)):
            results.append(
                {
                    "op": op_name,
                    "status": "skipped",
                    "reason": "manual_review_required",
                    "detail": op,
                }
            )
            continue
        if op_name == "change_field_type":
            results.append(
                {
                    "op": op_name,
                    "status": "skipped",
                    "reason": "type_change_not_supported",
                    "detail": op,
                }
            )
            continue

        if not execute:
            results.append(
                {
                    "op": op_name,
                    "status": "planned",
                    "detail": op,
                }
            )
            continue

        if op_name == "add_field":
            result = _execute_add_field(client, token, workspace_id, database_id, op)
        elif op_name == "rename_field":
            result = _execute_rename_field(client, token, workspace_id, database_id, op)
        elif op_name == "delete_field":
            result = _execute_delete_field(client, token, workspace_id, database_id, op)
        elif op_name == "update_select_options":
            result = _execute_update_select_options(client, token, workspace_id, database_id, op)
        else:
            result = {
                "op": op_name,
                "status": "skipped",
                "reason": "unsupported_operation",
                "detail": op,
            }
        results.append(result)

    after_resp = grid_lib.get_database_fields(client, token, workspace_id, database_id)
    after_fields_raw = after_resp.get("data", []) if isinstance(after_resp, dict) else []
    after_source_fields = [normalize_current_field(item) for item in after_fields_raw if isinstance(item, dict)]
    after_diff = build_schema_diff(after_source_fields, target_fields, rename_hint_threshold=1.0)
    return before_diff, results, after_diff


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rollback collab state from snapshot file. Default mode is dry-run. "
            "Execution requires --execute --yes."
        )
    )
    parser.add_argument("--snapshot-file", required=True, help="Snapshot JSON file from snapshot-collab.")
    parser.add_argument("--workspace-id", default=None, help="Override workspace id. Optional.")
    parser.add_argument("--database-id", default=None, help="Override database object id.")
    parser.add_argument("--view-id", default=None, help="Override doc object id.")
    parser.add_argument("--object-id", default=None, help="Override raw object id.")
    parser.add_argument("--collab-kind", choices=["doc", "database"], default=None)
    parser.add_argument("--allow-target-mismatch", action="store_true")
    parser.add_argument(
        "--strategy",
        choices=["auto", "state-update", "schema-database"],
        default="auto",
        help="Rollback strategy. auto: database->schema, others->state-update.",
    )
    parser.add_argument("--execute", action="store_true", help="Apply rollback. Default is dry-run.")
    parser.add_argument("--yes", action="store_true", help="Confirm rollback when --execute.")
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
        raise AppFlowyError("Rollback requires --yes when --execute is set.")

    snapshot = _load_json_with_fallback(args.snapshot_file)
    workspace_id, object_id, collab_type, collab_kind = _resolve_target(args, snapshot)
    snapshot_doc_state = snapshot.get("doc_state")
    snapshot_state_vector = snapshot.get("state_vector")
    if not isinstance(snapshot_doc_state, list) or not isinstance(snapshot_state_vector, list):
        raise AppFlowyError("Snapshot file missing doc_state/state_vector array.")
    snapshot_database_fields = snapshot.get("database_schema_fields")
    if snapshot_database_fields is not None and not isinstance(snapshot_database_fields, list):
        raise AppFlowyError("snapshot.database_schema_fields must be an array when provided.")
    snapshot_doc_hash = _sha256_of_int_list(snapshot_doc_state)
    snapshot_sv_hash = _sha256_of_int_list(snapshot_state_vector)

    audit = new_audit_log(
        action="rollback_collab",
        target={"type": collab_kind, "id": object_id},
        input_data={
            "snapshot_file": args.snapshot_file,
            "workspace_id": workspace_id,
            "object_id": object_id,
            "collab_type": collab_type,
            "execute": bool(args.execute),
            "confirmed": bool(args.yes),
            "allow_target_mismatch": bool(args.allow_target_mismatch),
            "strategy": args.strategy,
        },
    )

    try:
        client = build_client(args)
        token = resolve_token(args, client)

        current_doc_state, current_state_vector = grid_lib.fetch_collab_state(
            client, token, workspace_id, object_id, collab_type
        )
        current_doc_hash = _sha256_of_int_list(current_doc_state)
        current_sv_hash = _sha256_of_int_list(current_state_vector)

        report = new_change_report(
            action="rollback_collab",
            target_type=collab_kind,
            target_id=object_id,
            dry_run=dry_run,
            input_data={
                "snapshot_file": args.snapshot_file,
                "workspace_id": workspace_id,
                "object_id": object_id,
                "collab_type": collab_type,
                "execute": bool(args.execute),
                "confirmed": bool(args.yes),
                "allow_target_mismatch": bool(args.allow_target_mismatch),
                "strategy": args.strategy,
            },
        )

        resolved_strategy = args.strategy
        if resolved_strategy == "auto":
            if collab_type == grid_lib.DB_COLLAB_TYPE and isinstance(snapshot_database_fields, list):
                resolved_strategy = "schema-database"
            else:
                resolved_strategy = "state-update"

        set_before(
            report,
            current_doc_state_len=len(current_doc_state),
            current_state_vector_len=len(current_state_vector),
            current_doc_state_sha256=current_doc_hash,
            current_state_vector_sha256=current_sv_hash,
        )
        set_plan(
            report,
            strategy=resolved_strategy,
            snapshot_doc_state_len=len(snapshot_doc_state),
            snapshot_state_vector_len=len(snapshot_state_vector),
            snapshot_doc_state_sha256=snapshot_doc_hash,
            snapshot_state_vector_sha256=snapshot_sv_hash,
        )

        if current_doc_hash == snapshot_doc_hash:
            add_report_warning(report, "Current doc_state already equals snapshot doc_state.")
            add_warning(audit, "Current doc_state already equals snapshot doc_state.")

        rollback_verified = False
        strategy_result: dict[str, Any] = {"strategy": resolved_strategy}

        if resolved_strategy == "schema-database":
            if collab_type != grid_lib.DB_COLLAB_TYPE:
                raise AppFlowyError("schema-database strategy only supports database collab type.")
            if not isinstance(snapshot_database_fields, list):
                raise AppFlowyError(
                    "Snapshot missing database_schema_fields. "
                    "Please recreate snapshot using snapshot-collab for database."
                )
            before_diff, schema_results, after_diff = _execute_schema_rollback(
                client,
                token,
                workspace_id,
                object_id,
                snapshot_database_fields,
                execute=bool(args.execute),
            )
            rollback_verified = all(count == 0 for count in _schema_summary(after_diff).values())
            strategy_result = {
                "strategy": resolved_strategy,
                "before_diff_summary": _schema_summary(before_diff),
                "after_diff_summary": _schema_summary(after_diff),
                "operation_results": schema_results,
            }
            if not rollback_verified:
                add_report_warning(
                    report,
                    "Schema rollback finished but schema still differs from snapshot schema.",
                )
                add_warning(audit, "schema rollback finished but diff remains")
        else:
            if not dry_run:
                grid_lib.post_web_update(
                    client,
                    token,
                    workspace_id,
                    object_id,
                    collab_type,
                    snapshot_doc_state,
                )
            after_doc_state, after_state_vector = grid_lib.fetch_collab_state(
                client, token, workspace_id, object_id, collab_type
            )
            after_doc_hash = _sha256_of_int_list(after_doc_state)
            after_sv_hash = _sha256_of_int_list(after_state_vector)
            rollback_verified = after_doc_hash == snapshot_doc_hash
            strategy_result = {
                "strategy": resolved_strategy,
                "after_doc_state_len": len(after_doc_state),
                "after_state_vector_len": len(after_state_vector),
                "after_doc_state_sha256": after_doc_hash,
                "after_state_vector_sha256": after_sv_hash,
            }
            if not rollback_verified and not dry_run:
                add_report_warning(
                    report,
                    "Rollback applied but after doc_state hash does not match snapshot hash.",
                )
                add_warning(audit, "after doc_state hash != snapshot doc_state hash")

        set_after(
            report,
            applied=not dry_run,
            rollback_verified=rollback_verified if not dry_run else False,
            current_equals_snapshot=(current_doc_hash == snapshot_doc_hash),
            strategy_result=strategy_result,
        )
        set_summary(
            report,
            dry_run=dry_run,
            rollback_planned=True,
            rollback_applied=not dry_run,
            rollback_verified=rollback_verified if not dry_run else False,
        )

        finish_audit_log(
            audit,
            status="success" if (dry_run or rollback_verified) else "partial_failed",
            result={
                "rollback_applied": not dry_run,
                "rollback_verified": rollback_verified if not dry_run else False,
                "strategy": resolved_strategy,
            },
        )
        audit_path = write_audit_log(audit, args.audit_log_file)

        print_json(
            {
                "workspace_id": workspace_id,
                "object_id": object_id,
                "collab_type": collab_type,
                "collab_kind": collab_kind,
                "snapshot_file": args.snapshot_file,
                "dry_run": dry_run,
                "rollback_verified": rollback_verified if not dry_run else False,
                "strategy": resolved_strategy,
                "strategy_result": strategy_result,
                "audit_log_file": audit_path,
                "change_report": report,
            }
        )
        return 0 if (dry_run or rollback_verified) else 2
    except Exception as exc:  # noqa: BLE001
        add_error(audit, str(exc))
        finish_audit_log(audit, status="failed", result={"error": str(exc)})
        audit_path = write_audit_log(audit, args.audit_log_file)
        raise AppFlowyError(f"rollback-collab failed. audit_log_file={audit_path}. error={exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
