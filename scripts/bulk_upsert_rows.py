import argparse

import doc_grid_lib as grid_lib
from _common import build_client, load_json_payload, print_json, resolve_token
from appflowy_client import AppFlowyError
from change_report import add_error, add_item, new_change_report, set_after, set_before, set_plan, set_summary


def parse_rows_payload(payload: object) -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("rows")
    else:
        rows = None
    if not isinstance(rows, list):
        raise AppFlowyError("Rows payload must be a JSON array or object with 'rows' array.")

    parsed: list[dict] = []
    for idx, item in enumerate(rows):
        if not isinstance(item, dict):
            raise AppFlowyError(f"Row[{idx}] must be an object.")
        cells = item.get("cells")
        if not isinstance(cells, dict) or not cells:
            raise AppFlowyError(f"Row[{idx}] requires non-empty 'cells' object.")
        parsed.append(item)
    return parsed


def resolve_pre_hash(item: dict, idx: int, prefix: str) -> str:
    raw_pre_hash = item.get("pre_hash")
    if isinstance(raw_pre_hash, str) and raw_pre_hash.strip():
        return raw_pre_hash.strip()
    key = item.get("key")
    if isinstance(key, str) and key.strip():
        return f"{prefix}:{key.strip()}"
    raise AppFlowyError(f"Row[{idx}] missing pre_hash/key.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bulk upsert rows by pre_hash/key with summary and diff output."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--rows", default=None, help="Rows JSON string")
    parser.add_argument("--rows-file", default=None, help="Rows JSON file")
    parser.add_argument("--pre-hash-prefix", default="bulk", help="Used when row uses 'key'.")
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately on first failed row. Default continues and collects failures.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview upsert plan only.")
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

    rows_payload = load_json_payload(args.rows, args.rows_file)
    rows = parse_rows_payload(rows_payload)
    parsed_rows = []
    for idx, item in enumerate(rows):
        pre_hash = resolve_pre_hash(item, idx, args.pre_hash_prefix)
        parsed_rows.append(
            {
                "index": idx,
                "pre_hash": pre_hash,
                "cells": item["cells"],
            }
        )

    client = build_client(args)
    token = resolve_token(args, client)
    before_ids = set(grid_lib.list_row_ids(client, token, args.workspace_id, args.database_id))

    report = new_change_report(
        action="bulk_upsert_rows",
        target_type="database",
        target_id=args.database_id,
        dry_run=bool(args.dry_run),
        input_data={
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "rows_count": len(parsed_rows),
            "pre_hash_prefix": args.pre_hash_prefix,
            "stop_on_error": bool(args.stop_on_error),
        },
    )
    set_before(report, row_count_before=len(before_ids))
    set_plan(
        report,
        planned_count=len(parsed_rows),
        rows=[{"index": item["index"], "pre_hash": item["pre_hash"]} for item in parsed_rows],
    )

    if args.dry_run:
        set_after(report, applied=False, row_count_after=len(before_ids))
        set_summary(
            report,
            planned_count=len(parsed_rows),
            executed_count=0,
            added_count=0,
            updated_count=0,
            failed_count=0,
            skipped_count=len(parsed_rows),
        )
        print_json(
            {
                "workspace_id": args.workspace_id,
                "database_id": args.database_id,
                "dry_run": True,
                "rows_planned": len(parsed_rows),
                "change_report": report,
            }
        )
        return 0

    added_count = 0
    updated_count = 0
    failed_count = 0
    executed_count = 0

    working_ids = set(before_ids)
    for item in parsed_rows:
        idx = item["index"]
        pre_hash = item["pre_hash"]
        cells = item["cells"]
        try:
            row_id = grid_lib.upsert_database_row(
                client,
                token,
                args.workspace_id,
                args.database_id,
                pre_hash,
                cells,
            )
            existed_before = row_id in working_ids
            if existed_before:
                updated_count += 1
                op = "updated"
            else:
                added_count += 1
                op = "added"
                working_ids.add(row_id)
            executed_count += 1
            add_item(
                report,
                {
                    "index": idx,
                    "pre_hash": pre_hash,
                    "row_id": row_id,
                    "result": op,
                },
            )
        except Exception as exc:  # noqa: BLE001
            failed_count += 1
            add_error(report, f"Row[{idx}] failed: {exc}")
            add_item(
                report,
                {
                    "index": idx,
                    "pre_hash": pre_hash,
                    "result": "failed",
                    "error": str(exc),
                },
            )
            if args.stop_on_error:
                break

    after_ids = set(grid_lib.list_row_ids(client, token, args.workspace_id, args.database_id))
    skipped_count = len(parsed_rows) - executed_count - failed_count
    set_after(
        report,
        applied=True,
        row_count_after=len(after_ids),
        row_count_diff=len(after_ids) - len(before_ids),
    )
    set_summary(
        report,
        planned_count=len(parsed_rows),
        executed_count=executed_count,
        added_count=added_count,
        updated_count=updated_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
    )

    print_json(
        {
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "dry_run": False,
            "summary": report.get("summary", {}),
            "change_report": report,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
