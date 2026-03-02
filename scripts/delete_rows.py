import argparse
from collections import OrderedDict
from pathlib import Path

import doc_grid_lib as grid_lib
from _common import build_client, print_json, resolve_token
from appflowy_client import AppFlowyError


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
        text = Path(row_ids_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            row_id = line.strip()
            if row_id:
                items.append(row_id)
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

    removed_explicit: list[str] = []
    if matched_explicit:
        doc_state, state_vector = grid_lib.fetch_collab_state(
            client, token, args.workspace_id, args.database_id, grid_lib.DB_COLLAB_TYPE
        )
        update = grid_lib.run_node_delete_row_orders(
            doc_state,
            state_vector,
            matched_explicit,
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
        removed_explicit = matched_explicit

    removed_empty: list[str] = []
    if args.remove_empty:
        removed_empty = grid_lib.cleanup_default_rows(
            client,
            token,
            args.workspace_id,
            args.database_id,
            max_remove=args.max_empty_remove,
            view_ids=view_ids or None,
        )

    print_json(
        {
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "view_ids": view_ids,
            "explicit_row_ids_requested": explicit_row_ids,
            "explicit_row_ids_removed": removed_explicit,
            "explicit_row_ids_not_found": not_found_explicit,
            "empty_row_ids_removed": removed_empty,
            "note": "Rows are removed from row_orders by collab update (no HTTP DELETE row endpoint).",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

