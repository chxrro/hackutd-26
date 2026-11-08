"""Run simulated scenarios end to end and check what the belt does.

  uv run python -m haptic_hub.sim                    core + fault scenarios (gate)
  uv run python -m haptic_hub.sim --probes           also run the limitation probes
  uv run python -m haptic_hub.sim --list
  uv run python -m haptic_hub.sim pole_left head_turn --show
  uv run python -m haptic_hub.sim approach_wall --record sim-runs/
  uv run python -m haptic_hub.sim --perception vision   real models on synthetic renders (informational)
  uv run python -m haptic_hub.sim --motion flow          add optical-flow motion (as `haptic-hub run --motion flow`)

Exit status is 0 only if every gating scenario passes. Probes never fail the run.
Every result is a SIMULATION: synthetic camera, emulated radio, no motors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

BANNER = (
    "SIMULATION: synthetic glasses frames -> real WebSocket -> real hub -> real belt firmware logic "
    "(host build).\nNot glasses, phone, BLE or motor results; perception '{p}'{note}."
)


def _print_report(report, gating: bool) -> None:
    tag = "PASS" if report.passed else ("FAIL" if gating else "FINDING")
    print(f"\n[{tag}] {report.scenario}  (simulation, perception={report.perception})")
    for chk in report.checks:
        mark = "ok " if chk.passed else ("XX " if gating else "-- ")
        print(f"   {mark} {chk.name}: {chk.detail}")
    m, k = report.metrics, report.counters
    lat = m.get("latency_ms") or {}
    print(f"       metrics: danger coverage {m.get('danger_coverage')}, quiet-zone buzzing {m.get('quiet_zone_buzzing')}, "
          f"hub received->belt command median/p90 {lat.get('hub_total')} ms, "
          f"phone capture->hub {lat.get('capture_recv')} ms")  # fmt: skip
    print(f"       counters: {k}")


def _warm_up(perception, scenario) -> None:
    from haptic_hub.sim.render import Renderer
    from haptic_hub.types import Frame

    image = Renderer(scenario.camera).render(scenario.build()).image
    for seq in range(1, 4):
        perception.process(Frame(seq, image, time.monotonic(), source="sim-warmup"))


async def _main(args: argparse.Namespace) -> int:
    from haptic_hub.sim.runner import run_scenario
    from haptic_hub.sim.scenarios import SCENARIOS, select

    if args.list:
        for sc in SCENARIOS.values():
            print(f"{sc.name:<30} [{','.join(sorted(sc.tags))}] {sc.duration:>4.1f}s  {sc.description}")
        return 0
    tags = {"core", "fault"} | ({"probe"} if args.probes else set())
    chosen = select(args.scenarios or None, tags)
    note = " (ground truth stands in for the models, so model accuracy is NOT tested)" if args.perception == "oracle" else (
        " on synthetic renders; informational only")  # fmt: skip
    print(BANNER.format(p=args.perception, note=note))

    perception = args.perception
    if perception != "oracle":  # load the models once and reuse them across scenarios
        from haptic_hub.config import HubConfig
        from haptic_hub.sim.oracle import TruthStore
        from haptic_hub.sim.runner import make_perception

        print("loading models ...")
        cfg = HubConfig()
        cfg.perception.motion = args.motion
        perception = await asyncio.to_thread(make_perception, args.perception, cfg, TruthStore(), chosen[0])
        await asyncio.to_thread(_warm_up, perception, chosen[0])  # first inference is slow; keep it out of scenarios

    reports, started = [], time.monotonic()
    for sc in chosen:
        record = None
        if args.record:
            out = Path(args.record)
            record = out / f"{sc.name}.mp4" if out.suffix != ".mp4" or len(chosen) > 1 else out
        report = await run_scenario(sc, perception, record=record, show=args.show, motion=args.motion)
        gating = "probe" not in sc.tags and args.perception == "oracle"
        _print_report(report, gating)
        reports.append((report, gating))
        if record:
            print(f"       video: {record}")

    gate = [r for r, g in reports if g]
    failed = [r.scenario for r in gate if not r.passed]
    findings = [r.scenario for r, g in reports if not g and not r.passed]
    print(f"\nSIMULATION summary ({time.monotonic() - started:.0f} s): {len(gate) - len(failed)}/{len(gate)} gating "
          f"scenarios passed" + (f"; FAILED: {failed}" if failed else "")
          + (f"; findings (non-gating): {findings}" if findings else ""))  # fmt: skip
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        payload = {"simulation": True, "perception": args.perception, "motion": args.motion, "results": [
            {**r.summary(), "gating": g} for r, g in reports]}  # fmt: skip
        Path(args.json).write_text(json.dumps(payload, indent=2, default=str))
        print(f"wrote {args.json}")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m haptic_hub.sim", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    parser.add_argument("scenarios", nargs="*", help="scenario names (default: all core + fault)")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--probes", action="store_true", help="also run the limitation probes (non-gating)")
    parser.add_argument("--perception", default="oracle", choices=["oracle", "vision", "depth", "detector"],
                        help="oracle = ground truth (default); others load the real models (informational)")
    parser.add_argument("--motion", default="off", choices=["off", "flow"],
                        help="optical-flow approach/crossing risk on tracked boxes (perception/flow.py)")
    parser.add_argument("--record", help="write an MP4 per scenario into this directory (or one .mp4 path)")
    parser.add_argument("--show", action="store_true", help="live window (q to stop drawing)")
    parser.add_argument("--json", help="write a JSON summary to this path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")  # fmt: skip
    if not args.verbose:
        for noisy in ("websockets", "haptic_hub.sources.glasses", "haptic_hub.app"):  # injected faults are expected
            logging.getLogger(noisy).setLevel(logging.CRITICAL)
    try:
        sys.exit(asyncio.run(_main(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
