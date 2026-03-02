import argparse
from collections import OrderedDict
from pathlib import Path

import doc_grid_lib as grid_lib
from _common import build_client, print_json, resolve_token
from appflowy_client import AppFlowyError


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _collect_items(values: list[str] | None, csv_value: str | None, file_path: str | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        if value:
            items.append(value.strip())
    if csv_value:
        items.extend(_split_csv(csv_value))
    if file_path:
        text = Path(file_path).read_text(encoding="utf-8-sig")
        for line in text.splitlines():
            value = line.strip()
            if value:
                items.append(value)
    dedup = OrderedDict()
    for item in items:
        dedup[item] = True
    return list(dedup.keys())


def _resolve_target_fields(
    fields: list[dict],
    field_ids: list[str],
    field_names: list[str],
) -> list[dict]:
    by_id = {field.get("id"): field for field in fields if isinstance(field, dict) and field.get("id")}
    by_name: dict[str, list[dict]] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if not name:
            continue
        by_name.setdefault(name, []).append(field)

    targets: list[dict] = []
    for field_id in field_ids:
        target = by_id.get(field_id)
        if not target:
            raise AppFlowyError(f"Field id not found: {field_id}")
        targets.append(target)

    for field_name in field_names:
        matches = by_name.get(field_name, [])
        if not matches:
            raise AppFlowyError(f"Field name not found: {field_name}")
        if len(matches) > 1:
            ids = [item.get("id") for item in matches]
            raise AppFlowyError(
                f"Field name is ambiguous: {field_name}. Use --field-id instead. Matched ids: {ids}"
            )
        targets.append(matches[0])

    dedup = OrderedDict()
    for field in targets:
        dedup[field.get("id")] = field
    return list(dedup.values())


def _collect_field_references(database_json: dict, field_ids: set[str]) -> dict:
    database = database_json.get("data", {}).get("collab", {}).get("database", {})
    views = database.get("views", {})
    summary: dict[str, dict] = {field_id: {"view_hits": []} for field_id in field_ids}

    if not isinstance(views, dict):
        return summary

    for view_id, view in views.items():
        if not isinstance(view, dict):
            continue
        view_name = view.get("name")
        field_orders = view.get("field_orders") or []
        order_ids = []
        for item in field_orders:
            if isinstance(item, dict) and item.get("id"):
                order_ids.append(item.get("id"))

        field_settings = view.get("field_settings") or {}
        setting_ids = list(field_settings.keys()) if isinstance(field_settings, dict) else []
        filters = view.get("filters") or []
        sorts = view.get("sorts") or []
        groups = view.get("groups") or []

        for field_id in field_ids:
            hit = {
                "view_id": view_id,
                "view_name": view_name,
                "field_orders": order_ids.count(field_id),
                "field_settings": setting_ids.count(field_id),
                "filters": sum(
                    1 for item in filters if isinstance(item, dict) and item.get("field_id") == field_id
                ),
                "sorts": sum(
                    1 for item in sorts if isinstance(item, dict) and item.get("field_id") == field_id
                ),
                "groups": sum(
                    1 for item in groups if isinstance(item, dict) and item.get("field_id") == field_id
                ),
            }
            total = (
                hit["field_orders"]
                + hit["field_settings"]
                + hit["filters"]
                + hit["sorts"]
                + hit["groups"]
            )
            if total > 0:
                summary[field_id]["view_hits"].append(hit)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete database fields via collab update. Default mode is dry-run."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--field-id", action="append", default=[], help="Field id. Can be repeated.")
    parser.add_argument("--field-ids", default=None, help="Comma separated field ids.")
    parser.add_argument("--field-ids-file", default=None, help="UTF-8 file, one field id per line.")
    parser.add_argument("--field-name", action="append", default=[], help="Field name. Can be repeated.")
    parser.add_argument("--field-names", default=None, help="Comma separated field names.")
    parser.add_argument("--execute", action="store_true", help="Apply deletion. Default is dry-run.")
    parser.add_argument("--yes", action="store_true", help="Confirm destructive deletion when --execute.")
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
        raise AppFlowyError("Deletion requires --yes when --execute is set.")

    field_ids = _collect_items(args.field_id, args.field_ids, args.field_ids_file)
    field_names = _collect_items(args.field_name, args.field_names, None)
    if not field_ids and not field_names:
        raise AppFlowyError("Provide --field-id/--field-name (or csv/file variants).")

    client = build_client(args)
    token = resolve_token(args, client)

    fields_resp = grid_lib.get_database_fields(client, token, args.workspace_id, args.database_id)
    fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []
    targets = _resolve_target_fields(fields, field_ids, field_names)
    if not targets:
        raise AppFlowyError("No target field resolved.")

    primary_targets = [field for field in targets if bool(field.get("is_primary"))]
    if primary_targets:
        names = [field.get("name") for field in primary_targets]
        raise AppFlowyError(f"Primary field deletion is not allowed: {names}")

    target_ids = [field.get("id") for field in targets if field.get("id")]
    db_collab_json = grid_lib.fetch_collab_json(
        client, token, args.workspace_id, args.database_id, grid_lib.DB_COLLAB_TYPE
    )
    ref_summary = _collect_field_references(db_collab_json, set(target_ids))

    output = {
        "workspace_id": args.workspace_id,
        "database_id": args.database_id,
        "dry_run": dry_run,
        "targets": [
            {
                "id": field.get("id"),
                "name": field.get("name"),
                "field_type": field.get("field_type"),
                "is_primary": bool(field.get("is_primary")),
                "references": ref_summary.get(field.get("id"), {}).get("view_hits", []),
            }
            for field in targets
        ],
        "guardrails": {
            "primary_deletion_blocked": True,
            "execute_requires_yes": True,
        },
    }
    if dry_run:
        print_json(output)
        return 0

    doc_state, state_vector = grid_lib.fetch_collab_state(
        client, token, args.workspace_id, args.database_id, grid_lib.DB_COLLAB_TYPE
    )
    update = grid_lib.run_node_delete_fields(doc_state, state_vector, target_ids)
    grid_lib.post_web_update(
        client,
        token,
        args.workspace_id,
        args.database_id,
        grid_lib.DB_COLLAB_TYPE,
        update,
    )

    latest_resp = grid_lib.get_database_fields(client, token, args.workspace_id, args.database_id)
    latest_fields = latest_resp.get("data", []) if isinstance(latest_resp, dict) else []
    latest_ids = {field.get("id") for field in latest_fields if isinstance(field, dict)}
    output["deleted_field_ids"] = [field_id for field_id in target_ids if field_id not in latest_ids]
    output["still_present_field_ids"] = [field_id for field_id in target_ids if field_id in latest_ids]
    output["dry_run"] = False
    print_json(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
