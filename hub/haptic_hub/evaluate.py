"""Offline A/B replay: identical model outputs, different TTC/alert policies.

No frame uploads, belt, classifier service or image output. Model loading may
contact the model registry (and download missing weights). Reports behavior, not
accuracy: without human labels fewer alerts are not necessarily better.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from pathlib import Path

import cv2

from haptic_hub.config import HapticsConfig, PerceptionConfig
from haptic_hub.haptics import HapticPolicy
from haptic_hub.sources.replay import load_index
from haptic_hub.types import Frame, Health


class CachedModel:
    """Reset once per replay frame; both branches see the exact same prediction."""
    def __init__(self, model, method):
        self.model, self.method = model, method
        self.value = None

    def reset(self):
        self.value = None

    def _get(self, image):
        if self.value is None:
            self.value = getattr(self.model, self.method)(image)
        return self.value

    estimate = _get
    track = _get


def build_pair(device: str, motion: str):
    from haptic_hub.perception import build_tracker
    from haptic_hub.perception.models import DepthEstimator
    from haptic_hub.perception.vision import VisionPerception
    from haptic_hub.perception.flow import FlowMotionPerception

    cfg = PerceptionConfig(device=device, motion=motion)
    depth = CachedModel(DepthEstimator(cfg.depth_model, device), "estimate")
    tracker = CachedModel(build_tracker(cfg), "track")
    pair = {}
    for name in ("baseline", "stable"):
        pcfg = replace(cfg, robust_ttc=name == "stable")
        p = VisionPerception(pcfg, depth, tracker)
        if motion == "flow":
            p = FlowMotionPerception(p, pcfg.flow, pcfg.sectors, pcfg.ttc_near_s, pcfg.ttc_far_s)
        pair[name] = p
    return pair, (depth, tracker), cfg


def compare_frames(frames, pair, caches=()):
    """Dependency-injected core, also used by synthetic regression tests."""
    policies = {name: HapticPolicy(HapticsConfig(profile=name), 5) for name in pair}
    summaries = {name: {"alert_frames": 0, "sector_onsets": 0, "alert_seconds": 0.0,
                        "sector_alert_seconds": [0.0] * 5, "fault_frames": 0} for name in pair}
    rows, previous, last_time = [], {}, None
    covered_s = 0.0
    for frame in frames:
        if not math.isfinite(frame.received_at) or (last_time is not None and frame.received_at <= last_time):
            raise ValueError("replay timestamps must be finite and strictly increasing")
        dt = frame.received_at - last_time if last_time is not None else 0.0
        continuous = last_time is not None and dt <= 0.3
        if continuous:
            covered_s += dt
        for cache in caches:
            cache.reset()
        row = {"seq": frame.seq, "t": frame.received_at}
        for name, perception in pair.items():
            result = perception.process(frame)
            policy = policies[name]
            command = policy.command(result, Health(True))
            levels = command.levels
            active = [v > 0 and command.mode.name == "LEVELS" for v in levels]
            old = previous.get(name, [False] * 5) if continuous else [False] * 5
            s = summaries[name]
            s["alert_frames"] += int(any(active))
            s["fault_frames"] += int(command.mode.name == "FAULT")
            s["sector_onsets"] += sum(a and not b for a, b in zip(active, old))
            if continuous:
                s["alert_seconds"] += dt * any(old)
                for i in range(5):
                    s["sector_alert_seconds"][i] += dt * old[i]
            previous[name] = active
            row[name] = {"risks": [round(s.risk, 5) for s in result.sectors],
                         "evidence": [s.evidence for s in result.sectors],
                         "levels": list(levels), "mode": command.mode.name,
                         "decisions": list(policy.decisions)}
        rows.append(row)
        last_time = frame.received_at
    if not rows:
        raise ValueError("no frames to evaluate")
    return {"kind": "offline_replay_not_accuracy", "frames": len(rows),
            "observed_interval_s": covered_s, "summary": summaries, "rows": rows,
            "limits": "No ground truth, no real belt, no live latency measurement. "
                      "Intervals above 0.3 s excluded from duration totals; last frame has no duration."}


def load_frames(session: Path, max_frames: int):
    index = load_index(session)
    if not index:
        raise ValueError("recording contains no frames")
    origin = float(index[0]["received_at"])
    for entry in index[:max_frames]:
        path = (session / entry["file"]).resolve()
        if not path.is_relative_to(session.resolve()):
            raise ValueError("recording image path escapes session directory")
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"missing or unreadable recorded image: {entry['file']}")
        yield Frame(int(entry["seq"]), image, float(entry["received_at"]) - origin,
                    source="offline-replay")


async def evaluate_command(args):
    if args.max_frames <= 0:
        raise SystemExit("--max-frames must be positive")
    session, output = Path(args.session).expanduser(), Path(args.output).expanduser()
    if output.exists():
        raise SystemExit("report exists; choose a new --output path")
    if not (session / "frames.jsonl").is_file():
        raise SystemExit("--session must contain frames.jsonl")
    pair, caches, cfg = build_pair(args.device, args.motion)
    report = compare_frames(load_frames(session, args.max_frames), pair, caches)
    report["perception_configs"] = {
        p: asdict(replace(cfg, robust_ttc=p == "stable")) for p in pair
    }
    report["policy_configs"] = {p: asdict(HapticsConfig(profile=p)) for p in pair}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting someone else's report in a shared tree.
    with output.open("x") as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps({k: report[k] for k in ("kind", "frames", "observed_interval_s", "summary", "limits")}, indent=2))
    print(f"Report: {output}")
