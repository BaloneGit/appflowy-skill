from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_change_report(
    *,
    action: str,
    target_type: str,
    target_id: str,
    dry_run: bool,
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "timestamp": utc_now_iso(),
        "action": action,
        "target": {"type": target_type, "id": target_id},
        "dry_run": dry_run,
        "input": input_data or {},
        "before": {},
        "plan": {},
        "after": {},
        "summary": {},
        "items": [],
        "warnings": [],
        "errors": [],
    }


def set_before(report: dict[str, Any], **kwargs: Any) -> None:
    report.setdefault("before", {}).update(kwargs)


def set_plan(report: dict[str, Any], **kwargs: Any) -> None:
    report.setdefault("plan", {}).update(kwargs)


def set_after(report: dict[str, Any], **kwargs: Any) -> None:
    report.setdefault("after", {}).update(kwargs)


def set_summary(report: dict[str, Any], **kwargs: Any) -> None:
    report.setdefault("summary", {}).update(kwargs)


def add_item(report: dict[str, Any], item: dict[str, Any]) -> None:
    report.setdefault("items", []).append(item)


def add_warning(report: dict[str, Any], message: str) -> None:
    report.setdefault("warnings", []).append(message)


def add_error(report: dict[str, Any], message: str) -> None:
    report.setdefault("errors", []).append(message)
