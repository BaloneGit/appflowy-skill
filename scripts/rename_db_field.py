import argparse

import doc_grid_lib as grid_lib
from _common import build_client, print_json, resolve_token
from appflowy_client import AppFlowyError


def resolve_field(fields: list[dict], field_id: str | None, field_name: str | None) -> dict:
    if field_id:
        for field in fields:
            if isinstance(field, dict) and field.get("id") == field_id:
                return field
        raise AppFlowyError(f"Field id not found: {field_id}")

    if not field_name:
        raise AppFlowyError("Provide --field-id or --field-name.")

    matches = [field for field in fields if isinstance(field, dict) and field.get("name") == field_name]
    if not matches:
        raise AppFlowyError(f"Field name not found: {field_name}")
    if len(matches) > 1:
        ids = [item.get("id") for item in matches]
        raise AppFlowyError(
            f"Field name is ambiguous: {field_name}. Use --field-id instead. Matched ids: {ids}"
        )
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rename a database field via collab update. Supports lookup by field_id or field_name."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--field-id", default=None)
    parser.add_argument("--field-name", default=None)
    parser.add_argument("--new-name", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Preview rename only.")
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

    new_name = args.new_name.strip()
    if not new_name:
        raise AppFlowyError("--new-name cannot be empty.")

    client = build_client(args)
    token = resolve_token(args, client)
    fields_resp = grid_lib.get_database_fields(client, token, args.workspace_id, args.database_id)
    fields = fields_resp.get("data", []) if isinstance(fields_resp, dict) else []

    field = resolve_field(fields, args.field_id, args.field_name)
    field_id = field.get("id")
    current_name = field.get("name")
    if current_name == new_name:
        print_json(
            {
                "workspace_id": args.workspace_id,
                "database_id": args.database_id,
                "field_id": field_id,
                "old_name": current_name,
                "new_name": new_name,
                "changed": False,
                "note": "Field name unchanged.",
            }
        )
        return 0

    output = {
        "workspace_id": args.workspace_id,
        "database_id": args.database_id,
        "field_id": field_id,
        "old_name": current_name,
        "new_name": new_name,
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        print_json(output)
        return 0

    doc_state, state_vector = grid_lib.fetch_collab_state(
        client, token, args.workspace_id, args.database_id, grid_lib.DB_COLLAB_TYPE
    )
    update = grid_lib.run_node_rename_fields(
        doc_state,
        state_vector,
        [{"field_id": field_id, "new_name": new_name}],
    )
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
    latest_field = resolve_field(latest_fields, field_id, None)
    output["changed"] = latest_field.get("name") == new_name
    output["verified_name"] = latest_field.get("name")
    print_json(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
