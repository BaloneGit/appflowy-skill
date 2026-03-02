import argparse
import json
from datetime import datetime
from typing import Any

import doc_grid_lib as grid_lib
from _common import build_client, load_json_payload, print_json, resolve_token
from appflowy_client import AppFlowyError


def parse_query_payload(query: str | None, query_file: str | None) -> dict:
    if query or query_file:
        return load_json_payload(query, query_file)
    return {}


def as_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def comparable_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        dt = as_datetime(value)
        if dt is not None:
            return dt.timestamp()
        return value.lower()
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def compare_values(left: Any, right: Any) -> int:
    left_value = comparable_value(left)
    right_value = comparable_value(right)
    if left_value is None and right_value is None:
        return 0
    if left_value is None:
        return -1
    if right_value is None:
        return 1
    if type(left_value) is type(right_value):
        if left_value < right_value:
            return -1
        if left_value > right_value:
            return 1
        return 0
    left_text = str(left_value)
    right_text = str(right_value)
    if left_text < right_text:
        return -1
    if left_text > right_text:
        return 1
    return 0


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, dict):
        return len(value) == 0
    return False


def match_filter(cell_value: Any, op: str, expected: Any) -> bool:
    op = op.lower()
    if op == "eq":
        return cell_value == expected
    if op == "ne":
        return cell_value != expected
    if op == "contains":
        if isinstance(cell_value, str) and isinstance(expected, str):
            return expected.lower() in cell_value.lower()
        if isinstance(cell_value, list):
            return expected in cell_value
        return False
    if op == "in":
        if isinstance(expected, list):
            return cell_value in expected
        return False
    if op == "gt":
        return compare_values(cell_value, expected) > 0
    if op == "gte":
        return compare_values(cell_value, expected) >= 0
    if op == "lt":
        return compare_values(cell_value, expected) < 0
    if op == "lte":
        return compare_values(cell_value, expected) <= 0
    if op == "is_empty":
        return is_empty(cell_value)
    if op == "is_not_empty":
        return not is_empty(cell_value)
    raise AppFlowyError(f"Unsupported filter op: {op}")


def apply_filters(rows: list[dict], filters: list[dict]) -> list[dict]:
    if not filters:
        return rows
    result = []
    for row in rows:
        cells = row.get("cells", {}) if isinstance(row, dict) else {}
        matched = True
        for item in filters:
            field = item.get("field")
            op = item.get("op", "eq")
            expected = item.get("value")
            if not field:
                raise AppFlowyError("Filter requires field")
            value = cells.get(field) if isinstance(cells, dict) else None
            if not match_filter(value, op, expected):
                matched = False
                break
        if matched:
            result.append(row)
    return result


def sort_rows(rows: list[dict], sorts: list[dict]) -> list[dict]:
    if not sorts:
        return rows
    sorted_rows = list(rows)
    # Use stable sort from low-priority to high-priority keys.
    for sort in reversed(sorts):
        field = sort.get("field")
        direction = str(sort.get("direction", "asc")).lower()
        if not field:
            raise AppFlowyError("Sort requires field")
        reverse = direction == "desc"

        def key_func(row: dict) -> tuple[int, Any]:
            cells = row.get("cells", {}) if isinstance(row, dict) else {}
            value = cells.get(field) if isinstance(cells, dict) else None
            converted = comparable_value(value)
            return (1 if converted is None else 0, converted)

        sorted_rows.sort(key=key_func, reverse=reverse)
    return sorted_rows


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query database rows with client-side filter/sort/pagination."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--query", default=None, help="JSON query payload string")
    parser.add_argument("--query-file", default=None, help="JSON query payload file")
    parser.add_argument("--filter-field", action="append", default=[], help="Quick filter field")
    parser.add_argument("--filter-op", action="append", default=[], help="Quick filter op")
    parser.add_argument("--filter-value", action="append", default=[], help="Quick filter value")
    parser.add_argument("--sort-field", action="append", default=[], help="Quick sort field")
    parser.add_argument("--sort-direction", action="append", default=[], help="Quick sort direction")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=None)
    parser.add_argument("--with-doc", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=100)
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

    if args.chunk_size <= 0:
        raise AppFlowyError("--chunk-size must be > 0")

    query = parse_query_payload(args.query, args.query_file)
    filters = query.get("filter") or query.get("filters") or []
    sorts = query.get("sort") or query.get("sorts") or []
    limit = query.get("limit")
    offset = query.get("offset")
    with_doc = bool(query.get("with_doc", False))

    if args.filter_field:
        quick_filters = []
        count = len(args.filter_field)
        for idx, field in enumerate(args.filter_field):
            op = args.filter_op[idx] if idx < len(args.filter_op) else "eq"
            value = args.filter_value[idx] if idx < len(args.filter_value) else ""
            quick_filters.append({"field": field, "op": op, "value": value})
        filters.extend(quick_filters)

    if args.sort_field:
        quick_sorts = []
        for idx, field in enumerate(args.sort_field):
            direction = args.sort_direction[idx] if idx < len(args.sort_direction) else "asc"
            quick_sorts.append({"field": field, "direction": direction})
        sorts.extend(quick_sorts)

    if args.limit is not None:
        limit = args.limit
    if args.offset is not None:
        offset = args.offset
    if args.with_doc:
        with_doc = True

    limit = int(limit) if limit is not None else 50
    offset = int(offset) if offset is not None else 0
    if limit < 0 or offset < 0:
        raise AppFlowyError("limit/offset must be >= 0")

    client = build_client(args)
    token = resolve_token(args, client)

    row_ids = grid_lib.list_row_ids(client, token, args.workspace_id, args.database_id)
    details: list[dict] = []
    for ids in chunked(row_ids, args.chunk_size):
        if not ids:
            continue
        params = {"ids": ",".join(ids)}
        if with_doc:
            params["with_doc"] = "true"
        base = client._require_base_url()
        resp = client._request_json(
            "GET",
            f"{base}/api/workspace/{args.workspace_id}/database/{args.database_id}/row/detail",
            token=token,
            params=params,
        )
        chunk_rows = resp.get("data", []) if isinstance(resp, dict) else []
        details.extend(chunk_rows or [])

    filtered = apply_filters(details, filters)
    sorted_rows = sort_rows(filtered, sorts)
    paged = sorted_rows[offset : offset + limit] if limit > 0 else sorted_rows[offset:]

    print_json(
        {
            "workspace_id": args.workspace_id,
            "database_id": args.database_id,
            "total_row_ids": len(row_ids),
            "rows_loaded": len(details),
            "rows_after_filter": len(filtered),
            "rows_returned": len(paged),
            "limit": limit,
            "offset": offset,
            "with_doc": with_doc,
            "filter": filters,
            "sort": sorts,
            "data": paged,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
