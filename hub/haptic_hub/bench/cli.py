"""`haptic-hub bench`: wires the flow to a belt, an operator and a report file.

  haptic-hub bench                            real belt over BLE, interactive
  haptic-hub bench --serial /dev/cu.usbserial-10   + time TTL expiry from the belt's serial log
  haptic-hub bench --only motor-map           re-run one check
  haptic-hub bench --belt sim --yes           whole flow against the simulator (CI / dry run)

Exit code: 0 PASS, 1 FAIL, 3 INCOMPLETE (nothing failed, but some checks were
skipped or not confirmed by a person, e.g. with --yes), 130 aborted.
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from haptic_hub.bench.flow import SELECTABLE, AutoOperator, Bench, BenchSettings, ConsoleOperator, Operator
from haptic_hub.bench.links import BleBenchLink, EventLog, SimBenchLink
from haptic_hub.bench.report import DEFAULT_REPORT_DIR, SCHEMA, checks_to_json, exit_code, render_summary, verdict, write_report

SAFETY_BANNER = (
    "BENCH CHECK ONLY. Belt on the table, in a hand or worn while seated, with a sighted teammate.\n"
    "Not a walking test. Every buzz is short and carries a TTL; press q at any prompt (or Ctrl-C) to stop."
)

NOT_TESTED_BY_BENCH = [
    "obstacle detection, distance or time-to-collision (no camera or perception involved)",
    "end-to-end latency camera -> motor (write timing is the BLE write call only)",
    "BLE range and interference away from the bench",
    "battery life and motor heating over a long run",
]


def _ints(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in text.split(",") if x.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected comma-separated integers, got {text!r}") from exc


def _checks(text: str) -> frozenset[str]:
    names = frozenset(x.strip() for x in text.split(",") if x.strip())
    unknown = names - set(SELECTABLE)
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown check(s) {sorted(unknown)}; choose from {', '.join(SELECTABLE)}")
    return names


def add_bench_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "bench",
        help="hardware-day belt checks: motor map, levels, TTL failsafe, fault pattern, BLE write timing",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--belt", default="ble", choices=["ble", "sim"], help="ble = the real belt (default); sim = simulator")
    p.add_argument("--address", help="skip scanning for any HapticBelt and look for this BLE address/UUID")
    p.add_argument("--motors", type=int, default=5)
    p.add_argument("--level", type=int, default=200, help="buzz level for motor map / TTL / disconnect (0-255, default 200)")
    p.add_argument("--ramp", type=_ints, default=BenchSettings().ramp, help="levels for the level check (default 0,30,...,255)")
    p.add_argument("--ramp-motor", type=int, help="motor for the level check (default: centre)")
    p.add_argument("--ttl-ms", type=_ints, default=BenchSettings().ttls_ms, help="TTLs to test (default 250,1000; firmware clamps to 50-1000)")
    p.add_argument("--ttl-tolerance-ms", type=float, default=BenchSettings().ttl_tolerance_ms,
                   help="with --serial: how late link-lost may be logged after the TTL (default 250)")
    p.add_argument("--writes", type=int, default=100, help="writes per mode for the timing check (default 100)")
    p.add_argument("--serial", metavar="PORT", help="belt USB serial port (e.g. /dev/cu.usbserial-10) to time TTL expiry from its log")
    p.add_argument("--serial-settle-s", type=float, default=3.0, help="wait after opening the port (it may reset the board), default 3")
    p.add_argument("--only", type=_checks, help=f"comma-separated subset of: {', '.join(SELECTABLE)} (connect always runs)")
    p.add_argument("--yes", action="store_true", help="don't prompt; person-observed checks are reported UNCONFIRMED")
    p.add_argument("--scan-timeout", type=float, default=10.0)
    p.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="default hub/bench-reports (git-ignored)")
    p.add_argument("--note", help="free text saved in the report, e.g. 'belt v1, fresh power bank' (no names or personal data)")
    return p


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2, check=False,
                             cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _environment() -> dict[str, Any]:
    try:
        from importlib.metadata import version

        bleak_version = version("bleak")
    except Exception:  # noqa: BLE001 - informational only
        bleak_version = None
    return {"platform": platform.platform(), "python": sys.version.split()[0], "bleak": bleak_version, "hub_commit": _git_commit()}


def settings_from_args(args: argparse.Namespace) -> BenchSettings:
    try:
        return BenchSettings(
            motors=args.motors, level=args.level, ramp=args.ramp, ramp_motor=args.ramp_motor, ttls_ms=args.ttl_ms,
            ttl_tolerance_ms=args.ttl_tolerance_ms, writes=args.writes, scan_timeout_s=args.scan_timeout, only=args.only,
        )
    except ValueError as exc:
        raise SystemExit(f"bench: {exc}") from exc


async def run_bench(
    link: Any,
    operator: Operator,
    settings: BenchSettings,
    observer: EventLog | None = None,
    note: str | None = None,
    settle_s: float = 0.0,
) -> dict[str, Any]:
    """Runs every check and returns the report (not yet written)."""
    started_wall = datetime.now(UTC)
    started = time.monotonic()
    settle_until = None
    if observer is not None:
        await observer.start()
        if settle_s > 0:
            operator.say(f"Reading the belt log from {observer.source}; waiting {settle_s:.1f} s in case opening the port reset the board ...")
            await asyncio.sleep(settle_s)
        settle_until = time.monotonic()
    bench = Bench(link, operator, settings, observer)
    try:
        results = await bench.run(settle_until)
    finally:
        if observer is not None:
            await observer.stop()
    boot_at_open = observer is not None and any(e.kind == "boot" and e.t <= settle_until for e in observer.events)
    result = verdict(results, bool(bench.aborted))
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": "haptic-hub bench",
        "started_at": started_wall.isoformat(timespec="seconds"),
        "duration_s": round(time.monotonic() - started, 2),
        "file_stamp": started_wall.astimezone().strftime("%Y%m%d-%H%M%S"),
        "belt": link.kind,
        "simulated": bool(link.simulated),
        "verdict": result,
        "aborted": bench.aborted,
        "operator": "assumed (--yes)" if operator.assumed else "interactive",
        "observer": observer.source if observer else None,
        "serial_open_reset_board": boot_at_open if observer is not None and not link.simulated else None,
        "note": note,
        "settings": {k: (sorted(v) if isinstance(v, frozenset) else v) for k, v in asdict(settings).items()},
        "environment": _environment(),
        "connection": vars(bench.connection) if bench.connection else None,
        "checks": checks_to_json(results),
        "belt_log": [
            {"t_s": round(e.t - started, 3), "kind": e.kind, "line": e.line} for e in (observer.events[-500:] if observer else [])
        ],
        "not_tested_by_bench": NOT_TESTED_BY_BENCH + (["anything physical: this run used the simulated belt"] if link.simulated else []),
    }
    return report


async def bench_command(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    if args.belt == "sim":
        link: Any = SimBenchLink(settings.motors)
        observer: EventLog | None = link.observer
        if args.serial:
            raise SystemExit("bench: --serial is for the real belt; the simulator has its own log")
    else:
        link = BleBenchLink(settings.motors, args.address)
        observer = None
        if args.serial:
            from haptic_hub.bench.serial_log import SerialObserver

            observer = SerialObserver(args.serial)
    operator: Operator = AutoOperator() if args.yes else ConsoleOperator()
    operator.say(SAFETY_BANNER)
    if link.simulated:
        operator.say("SIMULATED BELT: nothing will vibrate.")
    try:
        report = await run_bench(link, operator, settings, observer, args.note, 0.0 if link.simulated else args.serial_settle_s)
    except OSError as exc:  # e.g. the serial port can't be opened
        raise SystemExit(f"bench: {exc}") from exc
    path = write_report(report, args.report_dir)
    print()
    print(render_summary(report))
    print(f"report: {path}")
    return exit_code(report["verdict"], bool(report["aborted"]))
