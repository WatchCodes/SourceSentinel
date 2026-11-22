import subprocess
from pathlib import Path

import pytest

from sourcesentinel.drift import (
    DriftDetectionError,
    behind_by_commits,
    detect_drift,
    diff_names,
    ls_remote_head,
)
from sourcesentinel.sentinel import ReportValidationError, build_upstream_report


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def git_out(cwd, *args):
    cp = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return cp.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init")
    git(path, "config", "user.name", "t")
    git(path, "config", "user.email", "t@t")
    return path


def commit_file(repo: Path, name: str, content: str, message: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git_out(repo, "rev-parse", "HEAD")


def make_upstream(tmp_path: Path):
    upstream = init_repo(tmp_path / "upstream")
    sha1 = commit_file(upstream, "a.txt", "one\n", "init")
    sha2 = commit_file(upstream, "a.txt", "two\n", "update")
    return upstream, sha1, sha2


class TestSentinelHardening:
    def test_build_upstream_report_deterministic(self):
        kwargs = dict(
            repo="WatchCodes/nastech-repo",
            upstream="Owner/Repo",
            upstream_sha="b" * 40,
            behind_by=3,
            detected_at="2026-01-01T00:00:00Z",
            upstream_state="active",
        )
        r1 = build_upstream_report(**kwargs)
        r2 = build_upstream_report(**kwargs)
        assert r1 == r2
        assert r1["detected_at"] == "2026-01-01T00:00:00Z"

    def test_build_upstream_report_rejects_bad_repo(self):
        with pytest.raises(ReportValidationError):
            build_upstream_report(
                repo="bad repo",
                upstream="Owner/Repo",
                upstream_sha="a" * 40,
                behind_by=0,
            )

    def test_build_upstream_report_rejects_bad_sha(self):
        with pytest.raises(ReportValidationError):
            build_upstream_report(
                repo="WatchCodes/nastech-repo",
                upstream="Owner/Repo",
                upstream_sha="A" * 40,
                behind_by=0,
            )

    def test_build_upstream_report_rejects_bad_behind_by(self):
        for bad in [-1, True, False, "1", 1.5]:
            with pytest.raises(ReportValidationError):
                build_upstream_report(
                    repo="WatchCodes/nastech-repo",
                    upstream="Owner/Repo",
                    upstream_sha="a" * 40,
                    behind_by=bad,
                )

    def test_build_upstream_report_rejects_bad_state(self):
        with pytest.raises(ReportValidationError):
            build_upstream_report(
                repo="WatchCodes/nastech-repo",
                upstream="Owner/Repo",
                upstream_sha="a" * 40,
                behind_by=0,
                upstream_state="unchanged",
            )

    def test_build_upstream_report_rejects_bad_detected_at(self):
        with pytest.raises(ReportValidationError):
            build_upstream_report(
                repo="WatchCodes/nastech-repo",
                upstream="Owner/Repo",
                upstream_sha="a" * 40,
                behind_by=0,
                detected_at="not-a-date",
            )

    def test_build_upstream_report_no_extra_keys(self):
        r = build_upstream_report(
            repo="WatchCodes/nastech-repo",
            upstream="Owner/Repo",
            upstream_sha="a" * 40,
            behind_by=0,
            detected_at="2026-01-01T00:00:00Z",
            upstream_state="active",
        )
        assert set(r.keys()) == {
            "schema_version",
            "repo",
            "upstream",
            "upstream_sha",
            "behind_by",
            "detected_at",
            "upstream_state",
        }


class TestDriftHardening:
    def test_detect_drift_deterministic(self, tmp_path: Path):
        upstream, sha1, sha2 = make_upstream(tmp_path)
        clone_dir = tmp_path / "clone"
        kwargs = dict(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=sha1,
            clone_dir=clone_dir,
            detected_at="2026-01-01T00:00:00Z",
        )
        r1 = detect_drift(**kwargs)
        r2 = detect_drift(**kwargs)
        assert r1["report"] == r2["report"]
        assert r1["changed_files"] == r2["changed_files"]
        assert r1["evidence"] == r2["evidence"]

    def test_detect_drift_clear_evidence(self, tmp_path: Path):
        upstream, sha1, sha2 = make_upstream(tmp_path)
        result = detect_drift(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=sha1,
            clone_dir=tmp_path / "clone",
            detected_at="2026-01-01T00:00:00Z",
        )
        evidence = result["evidence"]
        assert evidence["mapped_upstream_sha"] == sha1
        assert evidence["observed_upstream_sha"] == sha2
        assert evidence["behind_by"] == 1
        assert evidence["changed_file_count"] == 1
        assert evidence["upstream_state"] == "active"
        assert evidence["relation"] == "ancestor"
        assert evidence["local_only"] is False
        assert result["changed_files"] == ["a.txt"]

    def test_detect_drift_idempotent(self, tmp_path: Path):
        upstream, sha1, _ = make_upstream(tmp_path)
        clone_dir = tmp_path / "clone"
        r1 = detect_drift(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=sha1,
            clone_dir=clone_dir,
            detected_at="2026-01-01T00:00:00Z",
        )
        r2 = detect_drift(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=sha1,
            clone_dir=clone_dir,
            detected_at="2026-01-01T00:00:00Z",
        )
        assert r1 == r2

    def test_detect_drift_local_only_no_network(self, tmp_path: Path, monkeypatch):
        upstream, sha1, sha2 = make_upstream(tmp_path)
        clone_dir = tmp_path / "clone"
        # Prime a local clone using normal path once, then local-only must not touch network.
        detect_drift(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=sha1,
            clone_dir=clone_dir,
            detected_at="2026-01-01T00:00:00Z",
        )

        import sourcesentinel.drift as drift_mod

        real_run = drift_mod._run
        forbidden = {"ls-remote", "clone", "fetch"}

        def guarded(cmd, cwd=None, *, check=True):
            if any(part in forbidden for part in cmd[:3]):
                raise AssertionError(f"network command forbidden in local_only mode: {cmd}")
            return real_run(cmd, cwd=cwd, check=check)

        monkeypatch.setattr(drift_mod, "_run", guarded)
        result = detect_drift(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=sha1,
            clone_dir=clone_dir,
            detected_at="2026-01-01T00:00:00Z",
            head_sha=sha2,
            local_only=True,
        )
        assert result["report"]["behind_by"] == 1
        assert result["evidence"]["local_only"] is True

    def test_detect_drift_dry_run_alias_no_network(self, tmp_path: Path, monkeypatch):
        upstream, sha1, sha2 = make_upstream(tmp_path)
        clone_dir = tmp_path / "clone"
        detect_drift(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=sha1,
            clone_dir=clone_dir,
            detected_at="2026-01-01T00:00:00Z",
        )

        import sourcesentinel.drift as drift_mod

        real_run = drift_mod._run
        forbidden = {"ls-remote", "clone", "fetch"}

        def guarded(cmd, cwd=None, *, check=True):
            if any(part in forbidden for part in cmd[:3]):
                raise AssertionError(f"network command forbidden in dry_run mode: {cmd}")
            return real_run(cmd, cwd=cwd, check=check)

        monkeypatch.setattr(drift_mod, "_run", guarded)
        result = detect_drift(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=sha1,
            clone_dir=clone_dir,
            detected_at="2026-01-01T00:00:00Z",
            head_sha=sha2,
            dry_run=True,
        )
        assert result["evidence"]["local_only"] is True

    def test_detect_drift_local_only_requires_existing_clone(self, tmp_path: Path):
        upstream, sha1, sha2 = make_upstream(tmp_path)
        with pytest.raises(DriftDetectionError):
            detect_drift(
                mapping_repo="WatchCodes/nastech-test",
                upstream="Test/Repo",
                upstream_url=str(upstream),
                mapped_upstream_sha=sha1,
                clone_dir=tmp_path / "missing",
                head_sha=sha2,
                local_only=True,
            )

    def test_detect_drift_rejects_bad_sha(self, tmp_path: Path):
        upstream, sha1, _ = make_upstream(tmp_path)
        with pytest.raises(DriftDetectionError):
            detect_drift(
                mapping_repo="WatchCodes/nastech-test",
                upstream="Test/Repo",
                upstream_url=str(upstream),
                mapped_upstream_sha="not-a-sha",
                clone_dir=tmp_path / "clone",
            )

    def test_detect_drift_history_rewritten_fail_closed(self, tmp_path: Path):
        upstream = init_repo(tmp_path / "upstream")
        base = commit_file(upstream, "a.txt", "base\n", "base")
        base_branch = git_out(upstream, "branch", "--show-current")
        # Create divergent history from an unrelated root.
        git(upstream, "checkout", "--orphan", "rewrite")
        git(upstream, "rm", "-rf", ".")
        rewritten = commit_file(upstream, "a.txt", "rewritten\n", "rewrite")
        git(upstream, "checkout", base_branch)

        clone_dir = tmp_path / "clone"
        subprocess.run(
            ["git", "clone", "--no-tags", str(upstream), str(clone_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        git(clone_dir, "fetch", "origin", base_branch, "rewrite")
        result = detect_drift(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=base,
            clone_dir=clone_dir,
            head_sha=rewritten,
            local_only=True,
            detected_at="2026-01-01T00:00:00Z",
        )
        assert result["report"]["upstream_state"] == "history_rewritten"
        assert result["evidence"]["relation"] == "non_ancestor"

    def test_detect_drift_unchanged_is_active(self, tmp_path: Path):
        upstream = init_repo(tmp_path / "upstream")
        sha1 = commit_file(upstream, "a.txt", "same\n", "init")
        clone_dir = tmp_path / "clone"
        subprocess.run(["git", "clone", "--no-tags", str(upstream), str(clone_dir)], check=True, capture_output=True, text=True)
        result = detect_drift(
            mapping_repo="WatchCodes/nastech-test",
            upstream="Test/Repo",
            upstream_url=str(upstream),
            mapped_upstream_sha=sha1,
            clone_dir=clone_dir,
            head_sha=sha1,
            local_only=True,
            detected_at="2026-01-01T00:00:00Z",
        )
        assert result["report"]["behind_by"] == 0
        assert result["report"]["upstream_state"] == "active"
        assert result["changed_files"] == []


class TestDiffAndBehindHardening:
    def test_diff_names_sorted(self, tmp_path: Path):
        repo = init_repo(tmp_path / "repo")
        sha1 = commit_file(repo, "a.txt", "a\n", "init")
        (repo / "z.txt").write_text("z\n", encoding="utf-8")
        (repo / "b.txt").write_text("b\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "add")
        sha2 = git_out(repo, "rev-parse", "HEAD")
        assert diff_names(repo, sha1, sha2) == ["b.txt", "z.txt"]

    def test_behind_by_commits_rejects_missing_object(self, tmp_path: Path):
        repo = init_repo(tmp_path / "repo")
        sha1 = commit_file(repo, "a.txt", "a\n", "init")
        with pytest.raises(DriftDetectionError):
            behind_by_commits(repo, sha1, "f" * 40)

    def test_ls_remote_head_rejects_empty(self, monkeypatch):
        import sourcesentinel.drift as drift_mod

        class CP:
            stdout = ""
            stderr = ""
            returncode = 0

        monkeypatch.setattr(drift_mod, "_run", lambda *a, **k: CP())
        with pytest.raises(DriftDetectionError):
            ls_remote_head("https://example.invalid/repo.git", "HEAD")
