import argparse
from collections import OrderedDict

import doc_grid_lib as grid_lib
from _common import build_client, load_text_lines, print_json, resolve_token
from appflowy_client import AppFlowyError
from change_report import new_change_report, set_after, set_before, set_plan, set_summary


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _collect_row_ids(row_ids: list[str] | None, row_ids_csv: str | None, row_ids_file: str | None) -> list[str]:
    items: list[str] = []
    for row_id in row_ids or []:
        if row_id:
            items.append(row_id.strip())
    if row_ids_csv:
        items.extend(_split_csv(row_ids_csv))
    if row_ids_file:
        items.extend(load_text_lines(row_ids_file))
    dedup = OrderedDict()
    for item in items:
        dedup[item] = True
    return list(dedup.keys())


def _collect_view_ids(view_ids_csv: str | None) -> list[str]:
    if not view_ids_csv:
        return []
    dedup = OrderedDict()
    for view_id in _split_csv(view_ids_csv):
        dedup[view_id] = True
    return list(dedup.keys())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete database rows from grid row orders via collab web-update."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--row-id", action="append", default=[], help="Row id. Can be repeated.")
    parser.add_argument("--row-ids", default=None, help="Comma separated row ids.")
    parser.add_argument("--row-ids-file", default=None, help="UTF-8 file, one row id per line.")
    parser.add_argument(
        "--view-ids",
        default=None,
        help="Optional comma separated view ids. Defaults to all database views.",
    )
    parser.add_argument(
        "--remove-empty",
        action="store_true",
        help="Also remove empty rows using the same rule as template cleanup.",
    )
    parser.add_argument(
        "--max-empty-remove",
        type=int,
        default=100,
        help="Maximum rows removed when --remove-empty is enabled. Default: 100.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only.")
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

    explicit_row_ids = _collect_row_ids(args.row_id, args.row_ids, args.row_ids_file)
    if not explicit_row_ids and not args.remove_empty:
        raise AppFlowyError("Nothing to delete. Provide --row-id/--row-ids/--row-ids-file or --remove-empty.")

    if args.max_empty_remove <= 0:
        raise AppFlowyError("--max-empty-remove must be > 0.")

    client = build_client(args)
    token = resolve_token(args, client)
    view_ids = _collect_view_ids(args.view_ids)

    existing_ids = set(grid_lib.list_row_ids(client, token, args.workspace_id, args.database_id))
    matched_explicit = [row_id for row_id in explicit_row_ids if row_id in existing_ids]
    not_found_explicit = [row_id for row_id in explicit_row_ids if row_id not in existing_ids]

    empty_candidates: list[str] = []
    if args.remove_empty:
        empty_candidates = grid_lib.find_empty_row_ids(
            client,
            token,
            args.workspace_id,
            args.database_id,
            max_remove=args.max_empty_remove,
        )

    dedup_delete = OrderedDict()
    for row_id in matched_explicit:
        dedup_delete[row_id] = "explicit"
    for row_id in empty_candidates:
        dedup_delete[row_id] = "empty"
    planned_delete_ids = list(dedup_delete.keys())
    planned_explicit = [row_id for row_id in matched_explicit if row_id in dedup_delete]
    planned_empty = [row_id for row_id in empty_candidates if row_id in dedup_delete]

    report = new_change_report(
        action="delete_rows",
        target_type="database",
        target_id=args.database_id,
        dry_run=bool(args.dry_run),
        input_data={
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "view_ids": view_ids,
            "explicit_row_ids_requested": explicit_row_ids,
            "remove_empty": bool(args.remove_empty),
            "max_empty_remove": args.max_empty_remove,
        },
    )
    set_before(
        report,
        row_count_before=len(existing_ids),
        matched_explicit_count=len(matched_explicit),
        not_found_explicit_count=len(not_found_explicit),
        empty_candidate_count=len(empty_candidates),
    )
    set_plan(
        report,
        planned_delete_count=len(planned_delete_ids),
        planned_explicit_ids=planned_explicit,
        planned_empty_ids=planned_empty,
        not_found_explicit_ids=not_found_explicit,
    )

    deleted_row_ids: list[str] = []
    if not args.dry_run and planned_delete_ids:
        doc_state, state_vector = grid_lib.fetch_collab_state(
            client, token, args.workspace_id, args.database_id, grid_lib.DB_COLLAB_TYPE
        )
        update = grid_lib.run_node_delete_row_orders(
            doc_state,
            state_vector,
            planned_delete_ids,
            view_ids=view_ids or None,
        )
        grid_lib.post_web_update(
            client,
            token,
            args.workspace_id,
            args.database_id,
            grid_lib.DB_COLLAB_TYPE,
            update,
        )
        deleted_row_ids = planned_delete_ids

    after_ids = set(grid_lib.list_row_ids(client, token, args.workspace_id, args.database_id))
    set_after(
        report,
        row_count_after=len(after_ids),
        deleted_row_ids=deleted_row_ids,
        still_present_deleted_ids=[row_id for row_id in deleted_row_ids if row_id in after_ids],
    )
    set_summary(
        report,
        planned_delete_count=len(planned_delete_ids),
        deleted_count=len(deleted_row_ids),
        explicit_not_found_count=len(not_found_explicit),
    )

    print_json(
        {
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "view_ids": view_ids,
            "explicit_row_ids_requested": explicit_row_ids,
            "explicit_row_ids_not_found": not_found_explicit,
            "planned_explicit_row_ids": planned_explicit,
            "planned_empty_row_ids": planned_empty,
            "deleted_row_ids": deleted_row_ids,
            "dry_run": bool(args.dry_run),
            "note": "Rows are removed from row_orders by collab update (no HTTP DELETE row endpoint).",
            "change_report": report,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
