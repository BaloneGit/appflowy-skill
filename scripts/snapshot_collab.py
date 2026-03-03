from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import doc_grid_lib as grid_lib
from _common import build_client, print_json, resolve_token
from appflowy_client import AppFlowyError
from audit_log import add_error, finish_audit_log, new_audit_log, write_audit_log
from change_report import add_warning, new_change_report, set_after, set_before, set_plan, set_summary

COLLAB_KIND_TO_TYPE = {"doc": grid_lib.DOC_COLLAB_TYPE, "database": grid_lib.DB_COLLAB_TYPE}
COLLAB_TYPE_TO_KIND = {value: key for key, value in COLLAB_KIND_TO_TYPE.items()}


def _resolve_target(args) -> tuple[str, int, str]:
    provided = [bool(args.database_id), bool(args.view_id), bool(args.object_id)]
    if sum(provided) != 1:
        raise AppFlowyError("Provide exactly one target: --database-id or --view-id or --object-id.")
    if args.database_id:
        return args.database_id, grid_lib.DB_COLLAB_TYPE, "database"
    if args.view_id:
        return args.view_id, grid_lib.DOC_COLLAB_TYPE, "doc"
    if not args.collab_kind:
        raise AppFlowyError("--object-id requires --collab-kind {doc|database}.")
    collab_type = COLLAB_KIND_TO_TYPE[args.collab_kind]
    return args.object_id, collab_type, args.collab_kind


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


def _default_snapshot_path(workspace_id: str, object_id: str, kind: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_object = object_id.replace("/", "_")
    return str(Path(".tmp") / "snapshots" / f"{workspace_id}_{kind}_{safe_object}_{ts}.json")


def _write_json_file(path: str, payload: dict[str, Any]) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    return str(target)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture collab snapshot for database/doc and write snapshot file for rollback."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", default=None, help="Database object id (collab_type=database).")
    parser.add_argument("--view-id", default=None, help="View/Page object id (collab_type=doc).")
    parser.add_argument("--object-id", default=None, help="Raw object id. Requires --collab-kind.")
    parser.add_argument("--collab-kind", choices=["doc", "database"], default=None)
    parser.add_argument("--include-collab-json", action="store_true", help="Also store collab json payload.")
    parser.add_argument(
        "--snapshot-file",
        default=None,
        help="Output file path. Default: .tmp/snapshots/<workspace>_<kind>_<object>_<timestamp>.json",
    )
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

    object_id, collab_type, collab_kind = _resolve_target(args)
    snapshot_file = args.snapshot_file or _default_snapshot_path(args.workspace_id, object_id, collab_kind)

    audit = new_audit_log(
        action="snapshot_collab",
        target={"type": collab_kind, "id": object_id},
        input_data={
            "workspace_id": args.workspace_id,
            "object_id": object_id,
            "collab_type": collab_type,
            "include_collab_json": bool(args.include_collab_json),
            "snapshot_file": snapshot_file,
        },
    )

    try:
        client = build_client(args)
        token = resolve_token(args, client)

        doc_state, state_vector = grid_lib.fetch_collab_state(
            client, token, args.workspace_id, object_id, collab_type
        )
        payload: dict[str, Any] = {
            "schema_version": "v1",
            "snapshot_kind": "collab",
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "workspace_id": args.workspace_id,
            "object_id": object_id,
            "collab_type": collab_type,
            "collab_kind": collab_kind,
            "doc_state": doc_state,
            "state_vector": state_vector,
            "checksums": {
                "doc_state_sha256": _sha256_of_int_list(doc_state),
                "state_vector_sha256": _sha256_of_int_list(state_vector),
            },
            "stats": {
                "doc_state_len": len(doc_state),
                "state_vector_len": len(state_vector),
            },
        }
        if args.include_collab_json:
            collab_json = grid_lib.fetch_collab_json(
                client, token, args.workspace_id, object_id, collab_type
            )
            payload["collab_json"] = collab_json
        if collab_type == grid_lib.DB_COLLAB_TYPE:
            fields_resp = grid_lib.get_database_fields(client, token, args.workspace_id, object_id)
            fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []
            payload["database_schema_fields"] = fields

        written = _write_json_file(snapshot_file, payload)
        report = new_change_report(
            action="snapshot_collab",
            target_type=collab_kind,
            target_id=object_id,
            dry_run=True,
            input_data={
                "workspace_id": args.workspace_id,
                "object_id": object_id,
                "collab_type": collab_type,
                "include_collab_json": bool(args.include_collab_json),
                "snapshot_file": written,
            },
        )
        set_before(report, snapshot_exists_before=Path(written).exists())
        set_plan(
            report,
            snapshot_kind="collab",
            doc_state_len=len(doc_state),
            state_vector_len=len(state_vector),
            include_collab_json=bool(args.include_collab_json),
            include_database_schema_fields=(collab_type == grid_lib.DB_COLLAB_TYPE),
        )
        if len(doc_state) == 0:
            add_warning(report, "doc_state is empty.")
        set_after(
            report,
            snapshot_file=written,
            doc_state_sha256=payload["checksums"]["doc_state_sha256"],
            state_vector_sha256=payload["checksums"]["state_vector_sha256"],
        )
        set_summary(
            report,
            snapshot_created=True,
            doc_state_len=len(doc_state),
            state_vector_len=len(state_vector),
        )

        finish_audit_log(
            audit,
            status="success",
            result={
                "snapshot_file": written,
                "doc_state_len": len(doc_state),
                "state_vector_len": len(state_vector),
            },
        )
        audit_path = write_audit_log(audit, args.audit_log_file)

        print_json(
            {
                "workspace_id": args.workspace_id,
                "object_id": object_id,
                "collab_type": collab_type,
                "collab_kind": collab_kind,
                "snapshot_file": written,
                "checksums": payload["checksums"],
                "stats": payload["stats"],
                "audit_log_file": audit_path,
                "change_report": report,
            }
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        add_error(audit, str(exc))
        finish_audit_log(audit, status="failed", result={"error": str(exc)})
        audit_path = write_audit_log(audit, args.audit_log_file)
        raise AppFlowyError(f"snapshot-collab failed. audit_log_file={audit_path}. error={exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
