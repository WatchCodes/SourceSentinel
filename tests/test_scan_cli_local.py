"""CLI exposure of SourceSentinel's local-only/dry-run drift controls.

The Python API already supports these arguments; the CLI must pass them through
so a scheduler can run deterministic, network-free drift checks without a
shim or a custom script.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sourcesentinel.cli import main
from sourcesentinel.contracts import validate


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.name", "t")
    _git(path, "config", "user.email", "t@t")
    (path / "a.txt").write_text("one\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def _scan(repo_dir: Path, sha: str, *extra: str) -> int:
    # An unreachable URL proves local_only never touches the network.
    return main(
        [
            "scan",
            "--repo",
            "WatchCodes/nastech-repo",
            "--upstream",
            "Owner/Repo",
            "--upstream-url",
            "https://127.0.0.1:1/unreachable/invalid.git",
            "--mapped-sha",
            sha,
            "--clone-dir",
            str(repo_dir),
            *extra,
        ]
    )


def test_cli_scan_local_only_with_head_sha_is_network_free(tmp_path, capsys):
    sha = _init_repo(tmp_path / "upstream")
    code = _scan(
        tmp_path / "upstream",
        sha,
        "--local-only",
        "--head-sha",
        sha,
        "--detected-at",
        "2026-11-24T00:00:00Z",
    )
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["evidence"]["local_only"] is True
    assert result["evidence"]["relation"] == "equal"
    assert result["report"]["behind_by"] == 0
    assert result["report"]["upstream_sha"] == sha
    assert result["report"]["detected_at"] == "2026-11-24T00:00:00Z"
    assert result["changed_files"] == []
    validate("upstream-report", result["report"])


def test_cli_scan_dry_run_implies_local_only(tmp_path, capsys):
    sha = _init_repo(tmp_path / "upstream")
    code = _scan(tmp_path / "upstream", sha, "--dry-run", "--head-sha", sha)
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["evidence"]["local_only"] is True
    assert result["report"]["upstream_sha"] == sha


def test_cli_scan_local_only_resolves_head_from_ref(tmp_path, capsys):
    sha = _init_repo(tmp_path / "upstream")
    code = _scan(tmp_path / "upstream", sha, "--local-only", "--ref", "HEAD")
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["report"]["upstream_sha"] == sha
    assert result["evidence"]["ref"] == "HEAD"


def test_cli_scan_invalid_head_sha_fails_closed(tmp_path, capsys):
    sha = _init_repo(tmp_path / "upstream")
    code = _scan(
        tmp_path / "upstream", sha, "--json", "--local-only", "--head-sha", "not-a-sha"
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "head_sha" in payload["error"]


def test_cli_scan_local_only_missing_repo_fails_closed(tmp_path, capsys):
    sha = "a" * 40
    code = _scan(tmp_path / "absent", sha, "--json", "--local-only", "--head-sha", sha)
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
