import json
import subprocess
from pathlib import Path

import pytest

from sourcesentinel import contracts
from sourcesentinel.cli import main


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


def test_known_schema_names_include_upstream_report():
    names = contracts.known_schema_names()
    assert "upstream-report" in names
    assert "common" in names
    assert "migration-report" in names


def test_load_schema_unknown_fails_closed():
    with pytest.raises(contracts.ContractError):
        contracts.load_schema("not-a-real-schema")


def test_validate_accepts_valid_upstream_report():
    doc = {
        "schema_version": 1,
        "repo": "WatchCodes/nastech-repo",
        "upstream": "Owner/Repo",
        "upstream_sha": "b" * 40,
        "behind_by": 3,
        "detected_at": "2026-01-01T00:00:00Z",
    }
    contracts.validate("upstream-report", doc)


def test_validate_rejects_invalid_upstream_report():
    bad = {
        "schema_version": 1,
        "repo": "WatchCodes/nastech-repo",
        "upstream": "Owner/Repo",
        "upstream_sha": "not-a-sha",
        "behind_by": -1,
        "detected_at": "2026-01-01T00:00:00Z",
    }
    with pytest.raises(Exception):
        contracts.validate("upstream-report", bad)


def test_is_valid_unknown_schema_fails_closed():
    ok, errors = contracts.is_valid("nope", {"schema_version": 1})
    assert ok is False
    assert errors
    assert any("nope" in e or "Unknown schema" in e or "ContractError" in e for e in errors)


def test_validate_file_missing_fails_closed(tmp_path: Path):
    ok, errors = contracts.validate_file("upstream-report", tmp_path / "missing.json")
    assert ok is False
    assert errors


def test_validate_file_invalid_json_fails_closed(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{", encoding="utf-8")
    ok, errors = contracts.validate_file("upstream-report", p)
    assert ok is False
    assert errors


def test_validate_file_valid_document(tmp_path: Path):
    p = tmp_path / "report.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repo": "WatchCodes/nastech-repo",
                "upstream": "Owner/Repo",
                "upstream_sha": "a" * 40,
                "behind_by": 0,
                "detected_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    ok, errors = contracts.validate_file("upstream-report", p)
    assert ok is True
    assert errors == []


def test_cli_validate_ok(tmp_path: Path, capsys):
    p = tmp_path / "report.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repo": "WatchCodes/nastech-repo",
                "upstream": "Owner/Repo",
                "upstream_sha": "c" * 40,
                "behind_by": 1,
                "detected_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    code = main(["validate", "upstream-report", str(p), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["schema"] == "upstream-report"


def test_cli_validate_bad_schema_fails_closed(tmp_path: Path, capsys):
    p = tmp_path / "report.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        main(["validate", "not-a-schema", str(p)])
    assert ei.value.code != 0


def test_cli_validate_invalid_document(tmp_path: Path, capsys):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    code = main(["validate", "upstream-report", str(p), "--json"])
    out = capsys.readouterr().out
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["errors"]


def test_cli_report_emits_valid_report(capsys):
    code = main(
        [
            "report",
            "--repo",
            "WatchCodes/nastech-repo",
            "--upstream",
            "Owner/Repo",
            "--upstream-sha",
            "d" * 40,
            "--behind-by",
            "2",
            "--detected-at",
            "2026-01-01T00:00:00Z",
            "--upstream-state",
            "active",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    report = json.loads(out)
    assert report["repo"] == "WatchCodes/nastech-repo"
    assert report["behind_by"] == 2
    assert report["upstream_state"] == "active"
    contracts.validate("upstream-report", report)


def test_cli_report_invalid_fails_closed(capsys):
    code = main(
        [
            "report",
            "--repo",
            "WatchCodes/nastech-repo",
            "--upstream",
            "Owner/Repo",
            "--upstream-sha",
            "not-a-sha",
            "--behind-by",
            "0",
            "--json",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "error" in payload


def test_cli_scan_local_repo(tmp_path: Path, capsys):
    upstream = tmp_path / "upstream"
    sha1 = _init_repo(upstream)
    (upstream / "a.txt").write_text("two\n", encoding="utf-8")
    _git(upstream, "commit", "-am", "update")
    sha2 = _git(upstream, "rev-parse", "HEAD").stdout.strip()

    clone_dir = tmp_path / "clone"
    code = main(
        [
            "scan",
            "--repo",
            "WatchCodes/nastech-repo",
            "--upstream",
            "Owner/Repo",
            "--upstream-url",
            str(upstream),
            "--mapped-sha",
            sha1,
            "--clone-dir",
            str(clone_dir),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    result = json.loads(out)
    assert result["report"]["behind_by"] == 1
    assert result["report"]["upstream_sha"] == sha2
    assert "a.txt" in result["changed_files"]
    contracts.validate("upstream-report", result["report"])


def test_cli_scan_missing_args_fails_closed():
    with pytest.raises(SystemExit) as ei:
        main(["scan", "--repo", "WatchCodes/nastech-repo"])
    assert ei.value.code != 0


def _report_with_checkpoints(checkpoints):
    return {
        "schema_version": 1,
        "batch_id": "b1",
        "source": "Owner/Repo",
        "destination": "WatchCodes/nastech-repo",
        "status": "REQUIRES_REVIEW",
        "checks": {
            "created": False, "named": True, "owned": True,
            "gated": True, "license_kept": True, "credits": True,
        },
        "receipt": {
            "idempotency_key": "b1::Owner/Repo",
            "destination_repo_id": 1,
            "source_sha": "a" * 40,
            "destination_sha": "b" * 40,
            "checkpoints": checkpoints,
        },
        "license": {"detected": "MIT", "preserved": True},
        "attribution": {"notice": True, "credits": True},
        "error": "checks not satisfied: created",
    }


def test_migration_report_accepts_every_codefoundry_checkpoint_stage():
    stages = (
        "start", "validate", "receipt", "scan", "license", "clone",
        "brand", "credits", "gate", "ship", "report",
    )
    checkpoints = [{"stage": s, "at": "2026-11-24T00:00:00Z", "ok": True} for s in stages]
    ok, errors = contracts.is_valid("migration-report", _report_with_checkpoints(checkpoints))
    assert ok, errors


def test_migration_report_still_rejects_unknown_checkpoint_stage():
    checkpoints = [{"stage": "not_a_real_stage", "at": "2026-11-24T00:00:00Z", "ok": True}]
    ok, errors = contracts.is_valid("migration-report", _report_with_checkpoints(checkpoints))
    assert ok is False
    assert any("not_a_real_stage" in e for e in errors)
