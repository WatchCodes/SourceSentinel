"""A proven-absent upstream must emit a report, not silently produce nothing.

The upstream-report contract already declares ``missing``. An ambiguous error
(auth, rate limit, DNS, permission) must stay a hard error: only narrow evidence
of absence may become a report, and downstream must treat it as REQUIRES_REVIEW
rather than a rebuild trigger.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from sourcesentinel import drift
from sourcesentinel.contracts import validate

BASE_SHA = "a" * 40


class _Completed:
    stdout = ""
    stderr = ""
    returncode = 0


def _install_remote_failure(monkeypatch, detail: str) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        commands.append(list(cmd))
        if cmd[:2] == ["git", "fetch"]:
            return _Completed()
        if cmd[:2] == ["git", "ls-remote"]:
            raise drift.DriftCommandError(f"command failed: {' '.join(cmd)}: {detail}")
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr(drift, "_run", fake_run)
    return commands


def _detect(clone_dir, *, local_only: bool = False):
    return drift.detect_drift(
        mapping_repo="WatchCodes/nastech-repo",
        upstream="Test/Repo",
        upstream_url="https://example.invalid/repo.git",
        mapped_upstream_sha=BASE_SHA,
        clone_dir=clone_dir,
        detected_at="2026-01-01T00:00:00Z",
        local_only=local_only,
    )


def _clone_dir(tmp_path):
    path = tmp_path / "clone"
    subprocess.run(["git", "init", "--quiet", str(path)], check=True, capture_output=True, text=True)
    return path


def test_proven_missing_upstream_emits_schema_valid_report(tmp_path, monkeypatch):
    commands = _install_remote_failure(monkeypatch, "remote: Repository not found.")
    result = _detect(_clone_dir(tmp_path))

    report = result["report"]
    assert report["upstream_state"] == "missing"
    assert report["upstream_sha"] == BASE_SHA
    assert report["behind_by"] == 0
    validate("upstream-report", report)
    assert result["evidence"]["relation"] == "missing"
    assert result["evidence"]["observed_upstream_sha"] == BASE_SHA
    assert result["changed_files"] == []
    assert any(cmd[:2] == ["git", "ls-remote"] for cmd in commands)


def test_missing_report_never_contains_raw_remote_error(tmp_path, monkeypatch):
    _install_remote_failure(monkeypatch, "remote: Repository not found. token=secret-marker")
    result = _detect(_clone_dir(tmp_path))
    assert "secret-marker" not in json.dumps(result)
    assert "Repository not found" not in json.dumps(result)


def test_authentication_failure_is_not_classified_missing(tmp_path, monkeypatch):
    _install_remote_failure(monkeypatch, "remote: Authentication failed for 'https://example.invalid/repo.git'")
    with pytest.raises(drift.DriftCommandError):
        _detect(_clone_dir(tmp_path))


def test_rate_limit_failure_is_not_classified_missing(tmp_path, monkeypatch):
    _install_remote_failure(monkeypatch, "API rate limit exceeded for user")
    with pytest.raises(drift.DriftCommandError):
        _detect(_clone_dir(tmp_path))


def test_missing_marker_with_permission_denied_is_not_classified_missing(tmp_path, monkeypatch):
    _install_remote_failure(monkeypatch, "remote: Repository not found. remote: Permission denied")
    with pytest.raises(drift.DriftCommandError):
        _detect(_clone_dir(tmp_path))


def test_local_only_missing_clone_still_errors(tmp_path):
    with pytest.raises(drift.DriftDetectionError):
        _detect(tmp_path / "does-not-exist", local_only=True)
