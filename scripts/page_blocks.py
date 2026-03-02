import argparse
from typing import Any

import doc_grid_lib as grid_lib
from _common import build_client, print_json, resolve_token


def get_children_ids(block: dict, children_map: dict) -> list[str]:
    children_key = block.get("children")
    if not children_key:
        return []
    children = children_map.get(children_key, [])
    return children if isinstance(children, list) else []


def short_text(value: str, max_len: int = 80) -> str:
    text = value.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def build_tree(
    block_id: str,
    blocks: dict,
    children_map: dict,
    text_map: dict,
    visited: set[str],
) -> dict:
    if block_id in visited:
        return {"block_id": block_id, "cycle": True}
    visited.add(block_id)

    block = blocks.get(block_id)
    if not isinstance(block, dict):
        return {"block_id": block_id, "missing": True}

    text = grid_lib.block_text(blocks, children_map, text_map, block_id)
    children = []
    for child_id in get_children_ids(block, children_map):
        children.append(build_tree(child_id, blocks, children_map, text_map, visited))

    return {
        "block_id": block_id,
        "type": block.get("ty"),
        "external_id": block.get("external_id"),
        "text_preview": short_text(text),
        "children": children,
    }


def flatten_tree(tree: dict, level: int = 0) -> list[dict]:
    row = {
        "block_id": tree.get("block_id"),
        "type": tree.get("type"),
        "level": level,
        "text_preview": tree.get("text_preview"),
    }
    rows = [row]
    for child in tree.get("children", []) or []:
        if isinstance(child, dict):
            rows.extend(flatten_tree(child, level + 1))
    return rows


def extract_doc_struct(doc_json: dict) -> tuple[dict, dict, dict, list[str], str | None]:
    doc = doc_json.get("data", {}).get("collab", {}).get("document", {})
    blocks = doc.get("blocks", {})
    meta = doc.get("meta", {})
    children_map = meta.get("children_map", {})
    text_map = meta.get("text_map", {})
    page_id = doc.get("page_id")
    roots = []
    if isinstance(page_id, str):
        page_block = blocks.get(page_id) if isinstance(blocks, dict) else None
        if isinstance(page_block, dict):
            children_key = page_block.get("children")
            root_children = children_map.get(children_key, [])
            if isinstance(root_children, list):
                roots = root_children
        # Keep backward compatibility with old payload shape.
        if not roots:
            root_children = children_map.get(page_id, [])
            if isinstance(root_children, list):
                roots = root_children
    return blocks, children_map, text_map, roots, page_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Get page block tree from document collab JSON.")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--view-id", required=True)
    parser.add_argument("--raw", action="store_true", help="Output raw collab JSON response.")
    parser.add_argument(
        "--flat",
        action="store_true",
        help="Output flat rows with level/type/text instead of nested tree.",
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

    client = build_client(args)
    token = resolve_token(args, client)
    doc_json = grid_lib.fetch_collab_json(
        client, token, args.workspace_id, args.view_id, grid_lib.DOC_COLLAB_TYPE
    )
    if args.raw:
        print_json(doc_json)
        return 0

    blocks, children_map, text_map, roots, page_id = extract_doc_struct(doc_json)
    trees = []
    for root_id in roots:
        trees.append(build_tree(root_id, blocks, children_map, text_map, set()))

    data: Any = trees
    if args.flat:
        rows = []
        for tree in trees:
            rows.extend(flatten_tree(tree))
        data = rows

    print_json(
        {
            "workspace_id": args.workspace_id,
            "view_id": args.view_id,
            "page_id": page_id,
            "block_count": len(blocks) if isinstance(blocks, dict) else 0,
            "root_block_count": len(roots),
            "data": data,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
