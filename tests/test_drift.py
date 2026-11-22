import subprocess
from pathlib import Path

from sourcesentinel.drift import detect_drift


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_detect_drift_local(tmp_path: Path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init")
    git(upstream, "config", "user.name", "t")
    git(upstream, "config", "user.email", "t@t")
    (upstream / "a.txt").write_text("one\n", encoding="utf-8")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-m", "init")
    sha1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=upstream, capture_output=True, text=True, check=True).stdout.strip()
    (upstream / "a.txt").write_text("two\n", encoding="utf-8")
    git(upstream, "commit", "-am", "update")
    sha2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=upstream, capture_output=True, text=True, check=True).stdout.strip()

    clone_dir = tmp_path / "clone"
    result = detect_drift(
        mapping_repo="WatchCodes/nastech-test",
        upstream="Test/Repo",
        upstream_url=str(upstream),
        mapped_upstream_sha=sha1,
        clone_dir=clone_dir,
    )
    report = result["report"]
    assert report["behind_by"] == 1
    assert report["upstream_sha"] == sha2
    assert report["upstream_state"] == "active"
    assert "a.txt" in result["changed_files"]
