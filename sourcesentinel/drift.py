"""Drift detection helpers (local git operations).

The functions in this module are deterministic for a given git state, fail closed
on malformed input or ambiguous repository state, and avoid network side effects
when local_only=True.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .sentinel import build_upstream_report

# A hung remote or an interactive credential prompt must never wedge a
# scheduler. Every git invocation is bounded and non-interactive.
DEFAULT_COMMAND_TIMEOUT = 120

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_REF_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]{1,100}$")
_NOT_FOUND_RE = re.compile(r"repository '[^']+' not found", re.IGNORECASE)
# Absence is only inferred from narrow git diagnostics. Everything else —
# especially auth, permission, rate limit and DNS failures — must stay an
# error: a 404 caused by a revoked token is not proof the upstream vanished.
_ABSENCE_MARKERS = (
    "repository not found",
    "does not appear to be a git repository",
    "not a git repository",
)
_AMBIGUOUS_MARKERS = (
    "authentication",
    "authorization",
    "could not read username",
    "credential",
    "permission",
    "denied",
    "forbidden",
    "rate limit",
    "could not resolve host",
    "timed out",
    "timeout",
    "temporary failure",
    "ssl",
    "certificate",
    "proxy",
)


class DriftDetectionError(ValueError):
    """Raised when drift detection inputs or repository state are invalid."""


class DriftCommandError(RuntimeError):
    """Raised when a required git command fails."""


class _UpstreamMissing(DriftDetectionError):
    """Internal signal: remote resolution proved the upstream is absent."""


def _is_proven_missing(exc: DriftCommandError) -> bool:
    """Classify only narrow git diagnostics as proof of upstream absence.

    A 404 caused by a revoked credential or a masked private repo is not proof
    the upstream vanished, so any ambiguity marker wins over the absence text.
    """
    detail = str(exc).lower()
    if any(marker in detail for marker in _AMBIGUOUS_MARKERS):
        return False
    return any(marker in detail for marker in _ABSENCE_MARKERS) or bool(_NOT_FOUND_RE.search(detail))


def _run(
    cmd: list[str],
    cwd: str | Path | None = None,
    *,
    check: bool = True,
    timeout: int = DEFAULT_COMMAND_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        cp = subprocess.run(
            cmd, cwd=cwd, text=True, capture_output=True, check=False,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise DriftCommandError(
            f"command timed out after {timeout}s: {' '.join(cmd)}"
        ) from exc
    except OSError as exc:
        raise DriftCommandError(f"failed to execute {' '.join(cmd)}: {exc}") from exc
    if check and cp.returncode != 0:
        stderr = (cp.stderr or "").strip()
        stdout = (cp.stdout or "").strip()
        detail = stderr or stdout or f"exit {cp.returncode}"
        raise DriftCommandError(f"command failed: {' '.join(cmd)}: {detail}")
    return cp


def _require_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DriftDetectionError(f"{name} must be a non-empty string")
    return value


def _validate_sha(name: str, value: Any) -> str:
    text = _require_str(name, value)
    if not _SHA_RE.fullmatch(text):
        raise DriftDetectionError(f"{name} must be a full 40-character lowercase hex SHA")
    return text


def _validate_repo_ref(name: str, value: Any) -> str:
    text = _require_str(name, value)
    if not _REPO_REF_RE.fullmatch(text):
        raise DriftDetectionError(f"{name} must match owner/name repository ref format")
    return text


def _validate_ref(value: Any) -> str:
    text = _require_str("ref", value)
    if any(ch.isspace() for ch in text):
        raise DriftDetectionError("ref must not contain whitespace")
    if text.startswith("-"):
        raise DriftDetectionError("ref must not start with '-'")
    return text


def _validate_clone_dir(clone_dir: str | Path) -> Path:
    if isinstance(clone_dir, Path):
        return clone_dir
    if isinstance(clone_dir, str) and clone_dir.strip():
        return Path(clone_dir)
    raise DriftDetectionError("clone_dir must be a non-empty path")


def _git_dir(path: Path) -> bool:
    return (path / ".git").exists()


def _ensure_local_repo(clone_dir: Path) -> None:
    if not clone_dir.exists():
        raise DriftDetectionError(f"local clone_dir does not exist: {clone_dir}")
    if not _git_dir(clone_dir):
        raise DriftDetectionError(f"clone_dir is not a git repository: {clone_dir}")


def _ensure_object(repo_dir: Path, sha: str) -> None:
    cp = _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo_dir, check=False)
    if cp.returncode != 0:
        raise DriftDetectionError(f"required commit object not present locally: {sha}")


def ls_remote_head(repo_url: str, ref: str = "HEAD") -> str:
    """Resolve a remote ref to a full SHA. Network operation by design."""
    repo = _require_str("repo_url", repo_url)
    resolved_ref = _validate_ref(ref)
    cp = _run(["git", "ls-remote", repo, resolved_ref])
    lines = [line for line in cp.stdout.splitlines() if line.strip()]
    if not lines:
        raise DriftDetectionError(f"ref {resolved_ref!r} not found at remote {repo!r}")
    # Prefer exact ref match; fall back to first valid SHA.
    sha = None
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        candidate = parts[0]
        if not _SHA_RE.fullmatch(candidate):
            continue
        if len(parts) > 1 and parts[1] == resolved_ref:
            sha = candidate
            break
        if sha is None:
            sha = candidate
    if sha is None:
        raise DriftDetectionError(f"no valid SHA resolved for ref {resolved_ref!r} at {repo!r}")
    return sha


def behind_by_commits(repo_dir: str | Path, base_sha: str, head_sha: str) -> int:
    """Count commits in base_sha..head_sha. Both SHAs must exist locally."""
    repo = _validate_clone_dir(repo_dir)
    base = _validate_sha("base_sha", base_sha)
    head = _validate_sha("head_sha", head_sha)
    _ensure_local_repo(repo)
    _ensure_object(repo, base)
    _ensure_object(repo, head)
    cp = _run(["git", "rev-list", "--count", f"{base}..{head}"], cwd=repo)
    text = cp.stdout.strip()
    if not text.isdigit():
        raise DriftCommandError(f"unexpected rev-list count output: {text!r}")
    return int(text)


def _is_ancestor(repo_dir: Path, ancestor: str, descendant: str) -> bool:
    cp = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_dir,
        check=False,
    )
    if cp.returncode == 0:
        return True
    if cp.returncode == 1:
        return False
    detail = (cp.stderr or cp.stdout or "").strip() or f"exit {cp.returncode}"
    raise DriftCommandError(f"unable to evaluate ancestry: {detail}")


def diff_names(repo_dir: str | Path, base_sha: str, head_sha: str) -> list[str]:
    """Return deterministically sorted changed file names between two SHAs."""
    repo = _validate_clone_dir(repo_dir)
    base = _validate_sha("base_sha", base_sha)
    head = _validate_sha("head_sha", head_sha)
    _ensure_local_repo(repo)
    _ensure_object(repo, base)
    _ensure_object(repo, head)
    cp = _run(["git", "diff", "--name-only", base, head], cwd=repo)
    names = [line.strip() for line in cp.stdout.splitlines() if line.strip()]
    # Deterministic ordering regardless of git config/environment.
    return sorted(dict.fromkeys(names))


def _resolve_head(
    *,
    upstream_url: str,
    ref: str,
    head_sha: str | None,
    local_only: bool,
    clone_dir: Path,
) -> str:
    if head_sha is not None:
        return _validate_sha("head_sha", head_sha)
    if local_only:
        # Resolve from local repository refs without network.
        cp = _run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=clone_dir, check=False)
        if cp.returncode != 0:
            raise DriftDetectionError(
                "local_only mode requires head_sha or a locally resolvable ref"
            )
        resolved = cp.stdout.strip()
        return _validate_sha("head_sha", resolved)
    try:
        return ls_remote_head(upstream_url, ref)
    except DriftCommandError as exc:
        if _is_proven_missing(exc):
            raise _UpstreamMissing("upstream remote not found") from exc
        raise


def _missing_result(
    *,
    repo_ref: str,
    upstream_ref: str,
    upstream_url: str,
    base: str,
    ref: str,
    detected_at: str | None,
    local_only: bool,
) -> dict[str, Any]:
    """Emit a deterministic report for a proven-absent upstream."""
    report = build_upstream_report(
        repo=repo_ref,
        upstream=upstream_ref,
        upstream_sha=base,
        behind_by=0,
        detected_at=detected_at,
        upstream_state="missing",
    )
    evidence = {
        "mapping_repo": repo_ref,
        "upstream": upstream_ref,
        "upstream_url": upstream_url,
        "mapped_upstream_sha": base,
        "observed_upstream_sha": base,
        "ref": ref,
        "behind_by": 0,
        "upstream_state": "missing",
        "relation": "missing",
        "changed_file_count": 0,
        "local_only": local_only,
    }
    return {"report": report, "changed_files": [], "evidence": evidence}


def detect_drift(
    *,
    mapping_repo: str,
    upstream: str,
    upstream_url: str,
    mapped_upstream_sha: str,
    clone_dir: str | Path,
    ref: str = "HEAD",
    detected_at: str | None = None,
    head_sha: str | None = None,
    local_only: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Detect upstream drift and emit a schema-valid upstream report.

    local_only/dry_run forbids network side effects (clone/fetch/ls-remote).
    The caller must provide a local git clone_dir containing required objects.
    """
    repo_ref = _validate_repo_ref("mapping_repo", mapping_repo)
    upstream_ref = _validate_repo_ref("upstream", upstream)
    upstream_url_text = _require_str("upstream_url", upstream_url)
    base = _validate_sha("mapped_upstream_sha", mapped_upstream_sha)
    clone_path = _validate_clone_dir(clone_dir)
    resolved_ref = _validate_ref(ref)
    if not isinstance(local_only, bool) or not isinstance(dry_run, bool):
        raise DriftDetectionError("local_only and dry_run must be booleans")
    local_only = local_only or dry_run

    if local_only:
        _ensure_local_repo(clone_path)
    else:
        if not _git_dir(clone_path):
            _run(["git", "clone", "--no-tags", upstream_url_text, str(clone_path)])
        else:
            _run(["git", "fetch", "--all", "--prune"], cwd=clone_path)

    try:
        head = _resolve_head(
            upstream_url=upstream_url_text,
            ref=resolved_ref,
            head_sha=head_sha,
            local_only=local_only,
            clone_dir=clone_path,
        )
    except _UpstreamMissing:
        return _missing_result(
            repo_ref=repo_ref,
            upstream_ref=upstream_ref,
            upstream_url=upstream_url_text,
            base=base,
            ref=resolved_ref,
            detected_at=detected_at,
            local_only=local_only,
        )

    if not local_only:
        # Ensure both objects are available locally for deterministic comparison.
        _run(["git", "fetch", "origin", base, head], cwd=clone_path)
    else:
        _ensure_object(clone_path, base)
        _ensure_object(clone_path, head)

    if base == head:
        behind = 0
        changed: list[str] = []
        upstream_state = "active"
        relation = "equal"
    else:
        ancestor = _is_ancestor(clone_path, base, head)
        if ancestor:
            behind = behind_by_commits(clone_path, base, head)
            changed = diff_names(clone_path, base, head)
            upstream_state = "active"
            relation = "ancestor"
        else:
            # History rewritten/diverged: fail closed as history_rewritten with evidence.
            cp = _run(["git", "rev-list", "--count", f"{base}..{head}"], cwd=clone_path)
            text = cp.stdout.strip()
            if not text.isdigit():
                raise DriftCommandError(f"unexpected rev-list count output: {text!r}")
            behind = int(text)
            changed = diff_names(clone_path, base, head)
            upstream_state = "history_rewritten"
            relation = "non_ancestor"

    report = build_upstream_report(
        repo=repo_ref,
        upstream=upstream_ref,
        upstream_sha=head,
        behind_by=behind,
        detected_at=detected_at,
        upstream_state=upstream_state,
    )
    evidence = {
        "mapping_repo": repo_ref,
        "upstream": upstream_ref,
        "upstream_url": upstream_url_text,
        "mapped_upstream_sha": base,
        "observed_upstream_sha": head,
        "ref": resolved_ref,
        "behind_by": behind,
        "upstream_state": upstream_state,
        "relation": relation,
        "changed_file_count": len(changed),
        "local_only": local_only,
    }
    return {"report": report, "changed_files": changed, "evidence": evidence}
