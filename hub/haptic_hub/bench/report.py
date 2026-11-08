"""Bench results: one CheckResult per check, a verdict, a summary table and a
JSON report in hub/bench-reports/ (git-ignored)."""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[2] / "bench-reports"  # hub/bench-reports
SCHEMA = "haptic-hub-bench/1"

PASS, FAIL, UNCONFIRMED, SKIPPED, ERROR = "pass", "fail", "unconfirmed", "skipped", "error"
STATUSES = (PASS, FAIL, UNCONFIRMED, SKIPPED, ERROR)

# Exit codes for `haptic-hub bench`.
EXIT_PASS, EXIT_FAIL, EXIT_INCOMPLETE, EXIT_ABORTED = 0, 1, 3, 130


@dataclass
class CheckResult:
    name: str
    status: str
    summary: str
    # Who decided: "operator", "operator (assumed: --yes)", "serial log", "simulated firmware model", "automatic"
    observed_by: str = "automatic"
    data: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")


def verdict(checks: list[CheckResult], aborted: bool = False) -> str:
    """FAIL if anything failed or errored; INCOMPLETE if anything was skipped or
    left unconfirmed (or the run was aborted); else PASS."""
    statuses = {c.status for c in checks}
    if statuses & {FAIL, ERROR}:
        return "FAIL"
    if aborted or statuses & {SKIPPED, UNCONFIRMED} or not checks:
        return "INCOMPLETE"
    return "PASS"


def exit_code(result: str, aborted: bool = False) -> int:
    if result == "FAIL":
        return EXIT_FAIL
    if aborted:
        return EXIT_ABORTED
    return EXIT_PASS if result == "PASS" else EXIT_INCOMPLETE


def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank on an already sorted list (same rule as `nim-bench`)."""
    if not sorted_values:
        raise ValueError("no values")
    return sorted_values[min(len(sorted_values) - 1, round(q * (len(sorted_values) - 1)))]


def timing_stats(values_ms: list[float]) -> dict[str, float | int | None]:
    if not values_ms:
        return {"n": 0, "min_ms": None, "median_ms": None, "p90_ms": None, "max_ms": None}
    s = sorted(values_ms)
    return {
        "n": len(s),
        "min_ms": round(s[0], 3),
        "median_ms": round(statistics.median(s), 3),
        "p90_ms": round(percentile(s, 0.9), 3),
        "max_ms": round(s[-1], 3),
    }


def write_report(report: dict[str, Any], directory: Path | str = DEFAULT_REPORT_DIR, stamp: str | None = None) -> Path:
    """Writes `bench-<stamp>[-sim].json`, never overwriting an earlier report."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = stamp or str(report.get("file_stamp") or "report")
    base = f"bench-{stamp}" + ("-sim" if report.get("simulated") else "")
    path = directory / f"{base}.json"
    n = 2
    while path.exists():
        path = directory / f"{base}-{n}.json"
        n += 1
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=False, default=str) + "\n")
    tmp.replace(path)
    return path


def checks_to_json(checks: list[CheckResult]) -> list[dict[str, Any]]:
    return [asdict(c) for c in checks]


def render_summary(report: dict[str, Any]) -> str:
    checks = report.get("checks", [])
    width = max([len("check")] + [len(c["name"]) for c in checks])
    lines = []
    if report.get("simulated"):
        lines.append("SIMULATED BELT: nothing physical was tested. A pass here checks the bench, not the hardware.")
    lines.append(f"{'check':<{width}}  {'status':<11}  result")
    lines.append(f"{'-' * width}  {'-' * 11}  {'-' * 40}")
    for c in checks:
        lines.append(f"{c['name']:<{width}}  {c['status'].upper():<11}  {c['summary']}")
    lines.append("")
    v = report.get("verdict", "?")
    extra = ""
    if v == "INCOMPLETE":
        extra = " (nothing failed, but some checks were skipped or not confirmed by a person)"
    if report.get("aborted"):
        extra += " [aborted]"
    lines.append(f"verdict: {v}{extra}")
    return "\n".join(lines)
