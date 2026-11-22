"""Drift detection helpers (local git operations)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .sentinel import build_upstream_report


def _run(cmd: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=True)


def ls_remote_head(repo_url: str, ref: str = "HEAD") -> str:
    cp = _run(["git", "ls-remote", repo_url, ref])
    line = cp.stdout.strip().splitlines()[0]
    return line.split()[0]


def behind_by_commits(repo_dir: str | Path, base_sha: str, head_sha: str) -> int:
    # assumes both SHAs present locally
    cp = _run(["git", "rev-list", "--count", f"{base_sha}..{head_sha}"], cwd=repo_dir)
    return int(cp.stdout.strip() or "0")


def diff_names(repo_dir: str | Path, base_sha: str, head_sha: str) -> list[str]:
    cp = _run(["git", "diff", "--name-only", base_sha, head_sha], cwd=repo_dir)
    return [l for l in cp.stdout.strip().splitlines() if l.strip()]


def detect_drift(
    *,
    mapping_repo: str,
    upstream: str,
    upstream_url: str,
    mapped_upstream_sha: str,
    clone_dir: str | Path,
    ref: str = "HEAD",
) -> dict[str, Any]:
    clone_dir = Path(clone_dir)
    if not (clone_dir / ".git").exists():
        _run(["git", "clone", "--no-tags", upstream_url, str(clone_dir)])
    else:
        _run(["git", "fetch", "--all", "--prune"], cwd=clone_dir)
    head = ls_remote_head(upstream_url, ref)
    # ensure we have both objects
    _run(["git", "fetch", "origin", mapped_upstream_sha, head], cwd=clone_dir)
    behind = behind_by_commits(clone_dir, mapped_upstream_sha, head)
    changed = diff_names(clone_dir, mapped_upstream_sha, head)
    state = "active" if behind > 0 else "unchanged"
    report = build_upstream_report(
        repo=mapping_repo,
        upstream=upstream,
        upstream_sha=head,
        behind_by=behind,
        upstream_state=state,
    )
    return {"report": report, "changed_files": changed}
