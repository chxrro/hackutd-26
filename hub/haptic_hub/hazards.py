"""Attribution for the command already sent, not a second collision policy.

This additive phone snapshot never changes motor commands. Labels are only
carried from evidence that won the risk comparison; depth is unlabeled.
"""
from __future__ import annotations

import math
from haptic_hub.protocol import BeltCommand, BeltMode
from haptic_hub.types import PerceptionResult


def hazard_snapshot(result: PerceptionResult | None, command: BeltCommand,
                    healthy: bool, now: float, max_age_s: float = 1.0,
                    deadband: float = 0.12) -> dict:
    age = now - result.frame_received_at if result else None
    valid_age = age is not None and math.isfinite(age) and 0 <= age <= max_age_s
    snapshot = {"version": 1, "frame_seq": result.frame_seq if result else None,
                "result_age_ms": round(age * 1000, 1) if age is not None and math.isfinite(age) else None,
                "events": []}
    if not healthy or not valid_age or command.mode != BeltMode.LEVELS or result is None:
        return snapshot
    if len(result.sectors) != len(command.levels):
        return snapshot
    for i, (sector, level) in enumerate(zip(result.sectors, command.levels, strict=True)):
        if level <= 0:
            continue
        evidence = sector.evidence
        motion, label, track_id = sector.motion, sector.label, sector.track_id
        # A decaying motor command isn't evidence the previous object is still there.
        if sector.risk < deadband or evidence not in {"depth", "box_ttc", "flow_approach", "flow_crossing"}:
            evidence, motion, label, track_id = "unknown", "unknown", None, None
        elif evidence == "depth":
            motion, label, track_id = "unknown", None, None
        snapshot["events"].append({
            "id": f"{i}:{evidence}:{track_id if track_id is not None else '-'}",
            "sector": i, "level": level, "evidence": evidence,
            "motion": motion, "label": label, "track_id": track_id,
        })
    return snapshot
