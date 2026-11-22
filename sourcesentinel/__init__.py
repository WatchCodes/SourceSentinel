"""SourceSentinel - upstream drift detection and report emission."""

from .drift import (
    DriftCommandError,
    DriftDetectionError,
    behind_by_commits,
    detect_drift,
    diff_names,
    ls_remote_head,
)
from .sentinel import ReportValidationError, build_upstream_report, now_iso

__all__ = [
    "DriftCommandError",
    "DriftDetectionError",
    "ReportValidationError",
    "__version__",
    "behind_by_commits",
    "build_upstream_report",
    "detect_drift",
    "diff_names",
    "ls_remote_head",
    "now_iso",
]

__version__ = "0.1.0"
