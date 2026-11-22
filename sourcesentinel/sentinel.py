"""Upstream report emission."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from .contracts import validate


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_upstream_report(
    *,
    repo: str,
    upstream: str,
    upstream_sha: str,
    behind_by: int,
    detected_at: str | None = None,
    upstream_state: str | None = None,
) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "repo": repo,
        "upstream": upstream,
        "upstream_sha": upstream_sha,
        "behind_by": int(behind_by),
        "detected_at": detected_at or now_iso(),
    }
    if upstream_state is not None:
        report["upstream_state"] = upstream_state
    validate("upstream-report", report)
    return report
