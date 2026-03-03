from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_audit_log(
    *,
    action: str,
    target: dict[str, Any] | None = None,
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "action": action,
        "started_at": utc_now_iso(),
        "_started_perf": perf_counter(),
        "target": target or {},
        "input": input_data or {},
        "status": "running",
        "result": {},
        "errors": [],
        "warnings": [],
    }


def add_warning(log: dict[str, Any], message: str) -> None:
    log.setdefault("warnings", []).append(message)


def add_error(log: dict[str, Any], message: str) -> None:
    log.setdefault("errors", []).append(message)


def finish_audit_log(
    log: dict[str, Any],
    *,
    status: str,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_perf = float(log.pop("_started_perf", perf_counter()))
    log["finished_at"] = utc_now_iso()
    log["duration_ms"] = int((perf_counter() - started_perf) * 1000)
    log["status"] = status
    log["result"] = result or {}
    return log


def default_audit_log_path(action: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(".tmp") / "audit_logs" / f"{action}_{timestamp}.json"


def write_audit_log(log: dict[str, Any], output_file: str | None = None) -> str:
    path = Path(output_file) if output_file else default_audit_log_path(str(log.get("action") or "audit"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    return str(path)
