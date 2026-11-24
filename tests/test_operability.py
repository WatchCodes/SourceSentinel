"""Regression tests: standalone schemas, CLI entrypoint, and git timeouts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from sourcesentinel import contracts
from sourcesentinel.drift import DriftCommandError, _run

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_NAMES = tuple(contracts.SCHEMA_NAMES)


def test_packaged_schemas_resolve_without_env(monkeypatch):
    monkeypatch.delenv("WATCHCODES_SCHEMAS_DIR", raising=False)
    resolved = contracts._resolve_schemas_dir()
    assert resolved == (ROOT / "sourcesentinel" / "schemas").resolve()
    for filename in SCHEMA_NAMES:
        path = resolved / contracts.SCHEMA_NAMES[filename]
        assert path.is_file(), path
        json.loads(path.read_text(encoding="utf-8"))


def test_packaged_schemas_validate_a_document(monkeypatch):
    monkeypatch.delenv("WATCHCODES_SCHEMAS_DIR", raising=False)
    contracts.clear_caches()
    ok, errors = contracts.is_valid("common", {"schema_version": 1})
    assert ok, errors


def test_console_entrypoint_declared_in_pyproject():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"].get("scripts", {})
    assert scripts.get("sourcesentinel") == "sourcesentinel.cli:main"


def test_python_module_entrypoint():
    env = os.environ.copy()
    env.pop("WATCHCODES_SCHEMAS_DIR", None)
    cp = subprocess.run(
        [sys.executable, "-m", "sourcesentinel", "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0, cp.stderr


def test_run_has_finite_timeout(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _run(["git", "version"])
    assert captured.get("timeout") is not None
    env = captured.get("env")
    assert isinstance(env, dict) and env.get("GIT_TERMINAL_PROMPT") == "0"


def test_run_timeout_is_a_drift_command_error(monkeypatch):
    def timed_out(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(DriftCommandError, match="timed out"):
        _run(["git", "ls-remote", "https://example.invalid/repo.git", "HEAD"])
