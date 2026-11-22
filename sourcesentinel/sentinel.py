"""Upstream report emission.

This module is the deterministic, fail-closed boundary for upstream-report
documents. It validates inputs before emission and refuses malformed evidence.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .contracts import validate

_REPO_REF_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]{1,100}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_UPSTREAM_STATES = {"active", "archived", "missing", "history_rewritten"}


class ReportValidationError(ValueError):
    """Raised when upstream report inputs are malformed."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ReportValidationError(f"{name} must be a non-empty string")
    return value


def _validate_repo_ref(name: str, value: Any) -> str:
    text = _require_str(name, value)
    if not _REPO_REF_RE.fullmatch(text):
        raise ReportValidationError(f"{name} must match owner/name repository ref format")
    return text


def _validate_sha(name: str, value: Any) -> str:
    text = _require_str(name, value)
    if not _SHA_RE.fullmatch(text):
        raise ReportValidationError(f"{name} must be a full 40-character lowercase hex SHA")
    return text


def _validate_behind_by(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportValidationError("behind_by must be an integer >= 0")
    if value < 0:
        raise ReportValidationError("behind_by must be an integer >= 0")
    return value


def _validate_detected_at(value: Any) -> str:
    text = _require_str("detected_at", value)
    # Accept strict RFC3339/Z or offset forms parseable by datetime.fromisoformat.
    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ReportValidationError("detected_at must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ReportValidationError("detected_at must include timezone information")
    return text


def _validate_upstream_state(value: Any) -> str:
    text = _require_str("upstream_state", value)
    if text not in _UPSTREAM_STATES:
        allowed = ", ".join(sorted(_UPSTREAM_STATES))
        raise ReportValidationError(f"upstream_state must be one of: {allowed}")
    return text


def build_upstream_report(
    *,
    repo: str,
    upstream: str,
    upstream_sha: str,
    behind_by: int,
    detected_at: str | None = None,
    upstream_state: str | None = None,
) -> dict[str, Any]:
    """Build and schema-validate an upstream-report document.

    The function is deterministic for identical inputs. When detected_at is
    omitted, the current UTC time is used, so callers that need deterministic
    output must inject detected_at.
    """
    report: dict[str, Any] = {
        "schema_version": 1,
        "repo": _validate_repo_ref("repo", repo),
        "upstream": _validate_repo_ref("upstream", upstream),
        "upstream_sha": _validate_sha("upstream_sha", upstream_sha),
        "behind_by": _validate_behind_by(behind_by),
        "detected_at": _validate_detected_at(detected_at) if detected_at is not None else now_iso(),
    }
    if upstream_state is not None:
        report["upstream_state"] = _validate_upstream_state(upstream_state)
    validate("upstream-report", report)
    return report
