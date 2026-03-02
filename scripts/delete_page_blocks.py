import argparse
from collections import OrderedDict
from pathlib import Path

import doc_grid_lib as grid_lib
from _common import build_client, print_json, resolve_token
from appflowy_client import AppFlowyError


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _collect_block_ids(
    block_ids: list[str] | None,
    block_ids_csv: str | None,
    block_ids_file: str | None,
) -> list[str]:
    items: list[str] = []
    for block_id in block_ids or []:
        if block_id:
            items.append(block_id.strip())
    if block_ids_csv:
        items.extend(_split_csv(block_ids_csv))
    if block_ids_file:
        text = Path(block_ids_file).read_text(encoding="utf-8-sig")
        for line in text.splitlines():
            block_id = line.strip()
            if block_id:
                items.append(block_id)
    dedup = OrderedDict()
    for item in items:
        dedup[item] = True
    return list(dedup.keys())


def _get_children_ids(block_id: str, blocks: dict, children_map: dict) -> list[str]:
    block = blocks.get(block_id)
    if not isinstance(block, dict):
        return []
    children_key = block.get("children")
    if not children_key:
        return []
    children = children_map.get(children_key, [])
    return children if isinstance(children, list) else []


def _collect_delete_set(root_ids: list[str], blocks: dict, children_map: dict) -> set[str]:
    to_delete: set[str] = set()
    stack = list(root_ids)
    while stack:
        block_id = stack.pop()
        if block_id in to_delete:
            continue
        to_delete.add(block_id)
        for child_id in _get_children_ids(block_id, blocks, children_map):
            if child_id not in to_delete:
                stack.append(child_id)
    return to_delete


def _block_summary(block_id: str, blocks: dict, children_map: dict, text_map: dict) -> dict:
    block = blocks.get(block_id)
    if not isinstance(block, dict):
        return {"block_id": block_id, "missing": True}
    text = grid_lib.block_text(blocks, children_map, text_map, block_id)
    return {
        "block_id": block_id,
        "type": block.get("ty"),
        "external_id": block.get("external_id"),
        "text_preview": text[:120],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete page blocks via document collab web-update. Supports dry-run."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--view-id", required=True)
    parser.add_argument("--block-id", action="append", default=[], help="Block id. Can be repeated.")
    parser.add_argument("--block-ids", default=None, help="Comma separated block ids.")
    parser.add_argument("--block-ids-file", default=None, help="UTF-8 file, one block id per line.")
    parser.add_argument(
        "--ignore-missing",
        action="store_true",
        help="Ignore missing block ids instead of failing.",
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

    requested_ids = _collect_block_ids(args.block_id, args.block_ids, args.block_ids_file)
    if not requested_ids:
        raise AppFlowyError("Missing block ids. Provide --block-id/--block-ids/--block-ids-file.")

    client = build_client(args)
    token = resolve_token(args, client)

    doc_json = grid_lib.fetch_collab_json(
        client, token, args.workspace_id, args.view_id, grid_lib.DOC_COLLAB_TYPE
    )
    document = doc_json.get("data", {}).get("collab", {}).get("document", {})
    blocks = document.get("blocks", {})
    meta = document.get("meta", {})
    children_map = meta.get("children_map", {})
    text_map = meta.get("text_map", {})

    existing_set = set(blocks.keys()) if isinstance(blocks, dict) else set()
    matched_roots = [block_id for block_id in requested_ids if block_id in existing_set]
    missing_roots = [block_id for block_id in requested_ids if block_id not in existing_set]

    if missing_roots and not args.ignore_missing:
        raise AppFlowyError(
            f"Some block ids not found: {missing_roots}. Use --ignore-missing to skip missing ids."
        )
    if not matched_roots:
        raise AppFlowyError("No valid block ids to delete.")

    delete_set = _collect_delete_set(matched_roots, blocks, children_map)
    deleted_summaries = [
        _block_summary(block_id, blocks, children_map, text_map) for block_id in sorted(delete_set)
    ]
    root_summaries = [
        _block_summary(block_id, blocks, children_map, text_map) for block_id in matched_roots
    ]

    output = {
        "workspace_id": args.workspace_id,
        "view_id": args.view_id,
        "requested_block_ids": requested_ids,
        "root_block_ids": matched_roots,
        "missing_block_ids": missing_roots,
        "delete_count": len(delete_set),
        "root_blocks": root_summaries,
        "delete_blocks": deleted_summaries,
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        print_json(output)
        return 0

    doc_state, state_vector = grid_lib.fetch_collab_state(
        client, token, args.workspace_id, args.view_id, grid_lib.DOC_COLLAB_TYPE
    )
    update = grid_lib.run_node_delete_doc_blocks(doc_state, state_vector, matched_roots)
    grid_lib.post_web_update(
        client,
        token,
        args.workspace_id,
        args.view_id,
        grid_lib.DOC_COLLAB_TYPE,
        update,
    )

    output["applied"] = True
    print_json(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
