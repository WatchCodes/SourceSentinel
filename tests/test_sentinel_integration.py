import subprocess
from pathlib import Path

import pytest
from jsonschema import ValidationError

from sourcesentinel.contracts import validate
from sourcesentinel.drift import detect_drift
from sourcesentinel.sentinel import ReportValidationError, build_upstream_report


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def init_repo(path: Path, *, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", branch)
    git(path, "config", "user.name", "t")
    git(path, "config", "user.email", "t@t")
    return path


def commit_file(repo: Path, name: str, content: str, message: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", message)
    return (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
    )


def test_upstream_report_shape_required_fields():
    report = build_upstream_report(
        repo="WatchCodes/nastech-repo",
        upstream="Owner/Repo",
        upstream_sha="a" * 40,
        behind_by=0,
        detected_at="2026-01-01T00:00:00Z",
    )

    assert report == {
        "schema_version": 1,
        "repo": "WatchCodes/nastech-repo",
        "upstream": "Owner/Repo",
        "upstream_sha": "a" * 40,
        "behind_by": 0,
        "detected_at": "2026-01-01T00:00:00Z",
    }


def test_upstream_report_shape_with_upstream_state():
    report = build_upstream_report(
        repo="WatchCodes/nastech-repo",
        upstream="Owner/Repo",
        upstream_sha="b" * 40,
        behind_by=2,
        detected_at="2026-01-01T00:00:00Z",
        upstream_state="active",
    )

    assert report["schema_version"] == 1
    assert report["repo"] == "WatchCodes/nastech-repo"
    assert report["upstream"] == "Owner/Repo"
    assert report["upstream_sha"] == "b" * 40
    assert report["behind_by"] == 2
    assert report["detected_at"] == "2026-01-01T00:00:00Z"
    assert report["upstream_state"] == "active"
    assert set(report.keys()) == {
        "schema_version",
        "repo",
        "upstream",
        "upstream_sha",
        "behind_by",
        "detected_at",
        "upstream_state",
    }


def test_upstream_report_shape_from_drift_flow(tmp_path: Path):
    upstream = init_repo(tmp_path / "upstream")
    sha1 = commit_file(upstream, "a.txt", "one\n", "init")
    sha2 = commit_file(upstream, "a.txt", "two\n", "update")

    result = detect_drift(
        mapping_repo="WatchCodes/nastech-test",
        upstream="Test/Repo",
        upstream_url=str(upstream),
        mapped_upstream_sha=sha1,
        clone_dir=tmp_path / "clone",
    )
    report = result["report"]

    assert report["schema_version"] == 1
    assert report["repo"] == "WatchCodes/nastech-test"
    assert report["upstream"] == "Test/Repo"
    assert report["upstream_sha"] == sha2
    assert report["behind_by"] == 1
    assert report["upstream_state"] == "active"
    assert isinstance(report["detected_at"], str)
    assert report["detected_at"].endswith("Z")
    assert "T" in report["detected_at"]
    assert set(report.keys()) == {
        "schema_version",
        "repo",
        "upstream",
        "upstream_sha",
        "behind_by",
        "detected_at",
        "upstream_state",
    }


def test_upstream_report_shape_allows_zero_behind():
    report = build_upstream_report(
        repo="WatchCodes/nastech-repo",
        upstream="Owner/Repo",
        upstream_sha="f" * 40,
        behind_by=0,
        detected_at="2026-01-01T00:00:00Z",
        upstream_state="active",
    )

    assert report["behind_by"] == 0
    assert report["upstream_sha"] == "f" * 40
    assert report["upstream_state"] == "active"
    validate("upstream-report", report)


def test_drift_flow_reports_changed_files(tmp_path: Path):
    upstream = init_repo(tmp_path / "upstream")
    sha1 = commit_file(upstream, "a.txt", "one\n", "init")
    commit_file(upstream, "a.txt", "two\n", "update-a")
    sha3 = commit_file(upstream, "b.txt", "new\n", "add-b")

    result = detect_drift(
        mapping_repo="WatchCodes/nastech-test",
        upstream="Test/Repo",
        upstream_url=str(upstream),
        mapped_upstream_sha=sha1,
        clone_dir=tmp_path / "clone",
    )

    report = result["report"]
    assert report["behind_by"] == 2
    assert report["upstream_sha"] == sha3
    assert report["upstream_state"] == "active"
    assert sorted(result["changed_files"]) == ["a.txt", "b.txt"]


def test_idempotent_build_upstream_report_with_fixed_timestamp():
    kwargs = dict(
        repo="WatchCodes/nastech-repo",
        upstream="Owner/Repo",
        upstream_sha="c" * 40,
        behind_by=3,
        detected_at="2026-01-01T00:00:00Z",
        upstream_state="active",
    )
    first = build_upstream_report(**kwargs)
    second = build_upstream_report(**kwargs)

    assert first == second
    assert first is not second


def test_idempotent_detect_drift_same_inputs(tmp_path: Path):
    upstream = init_repo(tmp_path / "upstream")
    sha1 = commit_file(upstream, "a.txt", "one\n", "init")
    commit_file(upstream, "a.txt", "two\n", "update")
    clone_dir = tmp_path / "clone"

    kwargs = dict(
        mapping_repo="WatchCodes/nastech-test",
        upstream="Test/Repo",
        upstream_url=str(upstream),
        mapped_upstream_sha=sha1,
        clone_dir=clone_dir,
    )
    first = detect_drift(**kwargs)
    second = detect_drift(**kwargs)

    first_report = dict(first["report"])
    second_report = dict(second["report"])
    first_detected = first_report.pop("detected_at")
    second_detected = second_report.pop("detected_at")

    assert first_report == second_report
    assert isinstance(first_detected, str)
    assert isinstance(second_detected, str)
    assert first["changed_files"] == second["changed_files"]


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(
            repo="bad repo",
            upstream="Owner/Repo",
            upstream_sha="d" * 40,
            behind_by=0,
            detected_at="2026-01-01T00:00:00Z",
        ),
        dict(
            repo="WatchCodes/nastech-repo",
            upstream="bad upstream",
            upstream_sha="d" * 40,
            behind_by=0,
            detected_at="2026-01-01T00:00:00Z",
        ),
        dict(
            repo="WatchCodes/nastech-repo",
            upstream="Owner/Repo",
            upstream_sha="not-a-sha",
            behind_by=0,
            detected_at="2026-01-01T00:00:00Z",
        ),
        dict(
            repo="WatchCodes/nastech-repo",
            upstream="Owner/Repo",
            upstream_sha="d" * 40,
            behind_by=-1,
            detected_at="2026-01-01T00:00:00Z",
        ),
        dict(
            repo="WatchCodes/nastech-repo",
            upstream="Owner/Repo",
            upstream_sha="d" * 40,
            behind_by=0,
            detected_at="2026-01-01T00:00:00Z",
            upstream_state="unknown",
        ),
    ],
)
def test_build_upstream_report_rejects_invalid_inputs(kwargs):
    with pytest.raises((ValidationError, ReportValidationError)):
        build_upstream_report(**kwargs)


def test_validate_rejects_missing_required_field():
    report = {
        "schema_version": 1,
        "repo": "WatchCodes/nastech-repo",
        "upstream": "Owner/Repo",
        "upstream_sha": "e" * 40,
        "behind_by": 0,
    }
    with pytest.raises(ValidationError):
        validate("upstream-report", report)


def test_validate_rejects_additional_properties():
    report = {
        "schema_version": 1,
        "repo": "WatchCodes/nastech-repo",
        "upstream": "Owner/Repo",
        "upstream_sha": "e" * 40,
        "behind_by": 0,
        "detected_at": "2026-01-01T00:00:00Z",
        "unexpected": "value",
    }
    with pytest.raises(ValidationError):
        validate("upstream-report", report)


def test_validate_rejects_wrong_schema_version():
    report = {
        "schema_version": 2,
        "repo": "WatchCodes/nastech-repo",
        "upstream": "Owner/Repo",
        "upstream_sha": "e" * 40,
        "behind_by": 0,
        "detected_at": "2026-01-01T00:00:00Z",
    }
    with pytest.raises(ValidationError):
        validate("upstream-report", report)
