"""Schema loading and validation for WatchCodes contracts.

Fail-closed: unknown schemas, missing schema files, malformed schemas, or
validation errors are surfaced as errors rather than treated as success.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

SCHEMA_NAMES: dict[str, str] = {
    "discovery-manifest": "discovery-manifest.schema.json",
    "migration-request": "migration-request.schema.json",
    "repository-mapping": "repository-mapping.schema.json",
    "migration-report": "migration-report.schema.json",
    "upstream-report": "upstream-report.schema.json",
    "batch-readiness-report": "batch-readiness-report.schema.json",
    "common": "common.definitions.json",
}

_SCHEMA_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_REGISTRY_CACHE: dict[str, Any] = {}


class ContractError(ValueError):
    """Raised when a contract name, schema, or document is invalid."""


def known_schema_names() -> tuple[str, ...]:
    return tuple(SCHEMA_NAMES)


def _resolve_schemas_dir() -> Path:
    env = os.environ.get("WATCHCODES_SCHEMAS_DIR")
    if env:
        p = Path(env).expanduser()
        if not p.is_dir():
            raise ContractError(f"WATCHCODES_SCHEMAS_DIR is not a directory: {p}")
        return p.resolve()

    here = Path(__file__).resolve().parent
    # Prefer schemas shipped with the package so an installed wheel validates
    # the same contracts as CI, independent of a sibling checkout.
    candidates = [
        here / "schemas",
        here.parent.parent / "CodeFoundry" / "schemas",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    searched = ", ".join(str(c) for c in candidates)
    raise ContractError(f"Schema directory not found; searched: {searched}")


# Backward-compatible alias.
def _schemas_dir() -> Path:
    return _resolve_schemas_dir()


def _schema_path(name: str) -> Path:
    if not isinstance(name, str):
        raise ContractError(f"schema name must be str, got {type(name).__name__}")
    if name not in SCHEMA_NAMES:
        known = ", ".join(sorted(SCHEMA_NAMES))
        raise ContractError(f"Unknown schema {name!r}; known schemas: {known}")
    path = _resolve_schemas_dir() / SCHEMA_NAMES[name]
    if not path.is_file():
        raise ContractError(f"Schema file missing for {name!r}: {path}")
    return path


def clear_caches() -> None:
    _SCHEMA_CACHE.clear()
    _REGISTRY_CACHE.clear()


def load_schema(name: str) -> dict[str, Any]:
    path = _schema_path(name)
    cache_key = (str(path.parent.resolve()), name)
    cached = _SCHEMA_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ContractError(f"Schema {name!r} is not valid JSON: {path}: {e}") from e
    if not isinstance(data, dict):
        raise ContractError(f"Schema {name!r} must be a JSON object: {path}")
    _SCHEMA_CACHE[cache_key] = data
    return data


def _registry():
    from referencing import Registry, Resource

    schemas_dir = _resolve_schemas_dir()
    cache_key = str(schemas_dir)
    cached = _REGISTRY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    registry = Registry()
    for filename in SCHEMA_NAMES.values():
        path = schemas_dir / filename
        if not path.is_file():
            raise ContractError(f"Schema file missing: {path}")
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ContractError(f"Schema file is not valid JSON: {path}: {e}") from e
        if not isinstance(schema, dict):
            raise ContractError(f"Schema file must be a JSON object: {path}")
        resource = Resource.from_contents(schema)
        schema_id = schema.get("$id")
        if isinstance(schema_id, str) and schema_id:
            registry = registry.with_resource(schema_id, resource)
        registry = registry.with_resource(filename, resource)
    _REGISTRY_CACHE[cache_key] = registry
    return registry


def _format_error_path(error: ValidationError) -> str:
    return "$" + "".join(f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in error.path)


def _iter_errors(name: str, document: Any) -> list[ValidationError]:
    validator = Draft202012Validator(load_schema(name), registry=_registry())
    return sorted(validator.iter_errors(document), key=lambda e: (list(e.path), e.message))


def format_errors(name: str, errors: list[ValidationError]) -> list[str]:
    return [f"{name}{_format_error_path(err)}: {err.message}" for err in errors]


def validate(name: str, document: Any) -> None:
    """Validate document against named schema. Raises on failure."""
    try:
        errors = _iter_errors(name, document)
    except ContractError:
        raise
    except Exception as e:
        raise ContractError(f"{name}$: validation failed: {type(e).__name__}: {e}") from e
    if not errors:
        return
    msgs = format_errors(name, errors)
    summary = (
        f"{name} schema validation failed ({len(msgs)} error{'s' if len(msgs) != 1 else ''}):\n"
        + "\n".join(msgs)
    )
    err = ValidationError(summary)
    err.schema_name = name  # type: ignore[attr-defined]
    err.errors = msgs  # type: ignore[attr-defined]
    raise err


def is_valid(name: str, document: Any) -> tuple[bool, list[str]]:
    """Return (ok, errors). Fail closed on unknown schema or internal errors."""
    try:
        errors = _iter_errors(name, document)
    except Exception as e:
        return False, [f"{name}$: {type(e).__name__}: {e}"]
    if not errors:
        return True, []
    return False, format_errors(name, errors)


def load_json_file(path: str | Path) -> Any:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ContractError(f"JSON file not found: {p}") from e
    except OSError as e:
        raise ContractError(f"JSON file cannot be read: {p}: {e}") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ContractError(f"Invalid JSON in {p}: {e}") from e


def validate_file(name: str, path: str | Path) -> tuple[bool, list[str]]:
    """Validate a JSON file. Fail closed on read/parse/validation errors."""
    try:
        document = load_json_file(path)
    except Exception as e:
        return False, [f"{name}$: {type(e).__name__}: {e}"]
    return is_valid(name, document)
