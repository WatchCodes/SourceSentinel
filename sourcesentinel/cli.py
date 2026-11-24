"""Minimal CLI for SourceSentinel contract and drift flows."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .contracts import ContractError, is_valid, known_schema_names, load_json_file


def _emit(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _emit_error(message: str, *, as_json: bool) -> None:
    if as_json:
        _emit({"ok": False, "error": message})
    else:
        print(f"error: {message}", file=sys.stderr)


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        document = load_json_file(args.file)
    except Exception as e:
        msg = str(e)
        if args.json:
            _emit({"ok": False, "schema": args.schema, "file": args.file, "errors": [msg]})
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 1

    ok, errors = is_valid(args.schema, document)
    if args.json:
        payload: dict[str, Any] = {"ok": ok, "schema": args.schema, "file": args.file}
        if not ok:
            payload["errors"] = errors
        _emit(payload)
    else:
        if ok:
            print("OK")
        else:
            for err in errors:
                print(err, file=sys.stderr)
    return 0 if ok else 1


def _cmd_report(args: argparse.Namespace) -> int:
    from .sentinel import build_upstream_report

    try:
        report = build_upstream_report(
            repo=args.repo,
            upstream=args.upstream,
            upstream_sha=args.upstream_sha,
            behind_by=args.behind_by,
            detected_at=args.detected_at,
            upstream_state=args.upstream_state,
        )
    except Exception as e:
        _emit_error(f"{type(e).__name__}: {e}", as_json=args.json)
        return 1
    _emit(report)
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    from .drift import detect_drift

    try:
        result = detect_drift(
            mapping_repo=args.repo,
            upstream=args.upstream,
            upstream_url=args.upstream_url,
            mapped_upstream_sha=args.mapped_sha,
            clone_dir=args.clone_dir,
            ref=args.ref,
            detected_at=args.detected_at,
            head_sha=args.head_sha,
            local_only=args.local_only,
            dry_run=args.dry_run,
        )
    except Exception as e:
        _emit_error(f"{type(e).__name__}: {e}", as_json=args.json)
        return 1
    _emit(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sourcesentinel", description="SourceSentinel contract utilities.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Validate a JSON document against a contract schema.")
    p_validate.add_argument("schema", choices=sorted(known_schema_names()), help="Contract schema name.")
    p_validate.add_argument("file", help="Path to JSON document.")
    p_validate.add_argument("--json", action="store_true", default=False, help="Emit JSON output.")
    p_validate.set_defaults(func=_cmd_validate)

    p_report = sub.add_parser("report", help="Build and validate an UpstreamReport.")
    p_report.add_argument("--repo", required=True, help="Destination repository being monitored.")
    p_report.add_argument("--upstream", required=True, help="Upstream repository ref (owner/name).")
    p_report.add_argument("--upstream-sha", required=True, help="Upstream head SHA (40 hex).")
    p_report.add_argument("--behind-by", required=True, type=int, help="Commits behind upstream head.")
    p_report.add_argument("--detected-at", default=None, help="ISO-8601 detection timestamp.")
    p_report.add_argument("--upstream-state", default=None, choices=["active", "archived", "missing", "history_rewritten"], help="Optional upstream state.")
    p_report.add_argument("--json", action="store_true", default=False, help="Emit JSON errors.")
    p_report.set_defaults(func=_cmd_report)

    p_scan = sub.add_parser("scan", help="Scan upstream for drift and emit an UpstreamReport.")
    p_scan.add_argument("--repo", required=True, help="Destination repository being monitored.")
    p_scan.add_argument("--upstream", required=True, help="Upstream repository ref (owner/name).")
    p_scan.add_argument("--upstream-url", required=True, help="Upstream git URL or local path.")
    p_scan.add_argument("--mapped-sha", required=True, help="Mapped upstream SHA currently shipped.")
    p_scan.add_argument("--clone-dir", required=True, help="Local clone/cache directory.")
    p_scan.add_argument("--ref", default="HEAD", help="Upstream ref to check (default: HEAD).")
    p_scan.add_argument("--detected-at", default=None, help="ISO-8601 detection timestamp for the emitted report.")
    p_scan.add_argument("--head-sha", default=None, help="Pin the observed upstream SHA (40 hex); skips remote resolution.")
    p_scan.add_argument("--local-only", action="store_true", default=False, help="Resolve drift from the local clone only; never call the network.")
    p_scan.add_argument("--dry-run", action="store_true", default=False, help="Alias for --local-only: forbid clone/fetch/ls-remote side effects.")
    p_scan.add_argument("--json", action="store_true", default=False, help="Emit JSON errors.")
    p_scan.set_defaults(func=_cmd_scan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        raise SystemExit(code) from e
    try:
        return int(args.func(args))
    except ContractError as e:
        as_json = bool(getattr(args, "json", False))
        _emit_error(str(e), as_json=as_json)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # fail closed
        as_json = bool(getattr(args, "json", False))
        _emit_error(f"{type(e).__name__}: {e}", as_json=as_json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
