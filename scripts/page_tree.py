import argparse

from _common import build_client, print_json, resolve_token


def compact_view(node: dict) -> dict:
    children = node.get("children") or []
    compact_children = []
    for child in children:
        if isinstance(child, dict):
            compact_children.append(compact_view(child))
    return {
        "view_id": node.get("view_id"),
        "name": node.get("name"),
        "layout": node.get("layout"),
        "is_space": node.get("is_space"),
        "children": compact_children,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Get workspace page tree (folder view).")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--root-view-id", default=None)
    parser.add_argument("--compact", action="store_true", help="Only output compact tree fields.")
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
    base = client._require_base_url()
    params = {"depth": args.depth}
    if args.root_view_id:
        params["root_view_id"] = args.root_view_id
    result = client._request_json(
        "GET",
        f"{base}/api/workspace/{args.workspace_id}/folder",
        token=token,
        params=params,
    )

    if args.compact and isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict):
            result = {
                "code": result.get("code"),
                "message": result.get("message"),
                "workspace_id": args.workspace_id,
                "depth": args.depth,
                "root_view_id": args.root_view_id or args.workspace_id,
                "data": compact_view(data),
            }

    print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

