"""Ambient git environment must never redirect repository reads.

``detect_drift(clone_dir=...)`` promises to report on that clone. Inherited
GIT_DIR/GIT_WORK_TREE-style variables silently override ``git -C``, so a
scheduler with such a variable can receive a valid-looking report derived
from a different repository.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from sourcesentinel import drift

DETECTED_AT = "2026-01-01T00:00:00Z"
REPO_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_GRAFT_FILE",
    "GIT_SHALLOW_FILE",
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _make_history(root: Path) -> tuple[str, str]:
    root.mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "user.email", "t@t")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "one")
    first = _git(root, "rev-parse", "HEAD")
    (root / "a.txt").write_text("two\n", encoding="utf-8")
    _git(root, "commit", "-am", "two")
    second = _git(root, "rev-parse", "HEAD")
    return first, second


def test_local_only_resolves_head_from_clone_dir_not_ambient_git_dir(
    tmp_path: Path, monkeypatch
) -> None:
    clone = tmp_path / "clone"
    first, second = _make_history(clone)

    # Full object store, but HEAD intentionally left at the older commit.
    other = tmp_path / "other"
    _git(tmp_path, "clone", "--no-local", str(clone), str(other))
    _git(other, "reset", "--hard", first)

    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    result = drift.detect_drift(
        mapping_repo="WatchCodes/nastech-repo",
        upstream="Test/Repo",
        upstream_url=str(clone),
        mapped_upstream_sha=first,
        clone_dir=clone,
        local_only=True,
        detected_at=DETECTED_AT,
    )

    assert result["report"]["upstream_sha"] == second
    assert result["report"]["behind_by"] == 1
    assert result["evidence"]["observed_upstream_sha"] == second


def test_run_strips_repo_location_overrides(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_run(cmd, *args, **kwargs):
        captured.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(drift.subprocess, "run", fake_run)
    for key in REPO_LOCATION_VARS:
        monkeypatch.setenv(key, "/tmp/ambient-override")

    drift._run(["git", "rev-parse", "HEAD"])

    for key in REPO_LOCATION_VARS:
        assert key not in captured
    assert captured["GIT_TERMINAL_PROMPT"] == "0"
