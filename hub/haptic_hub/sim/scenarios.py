"""The scenario library. Each scenario builds a fresh world, optionally injects
faults, and lists checks on what the belt does.

Geometry notes (default CameraModel, 60 deg horizontal FOV, 5 sectors): sector
boundaries sit at about -30, -19, -6.6, +6.6, +19, +30 deg from the camera axis.
With the default thresholds a static obstacle starts to buzz below ~2.27 m
z-depth (depth risk 0 at 2.5 m, deadband 0.12), and a tracked person starts to
buzz once the box-growth TTC drops under 3 s.

Tags:
  core   pipeline behavior that should hold today (gates the run)
  fault  failure injection: stale camera, disconnects, crashes, lost belt writes
  probe  known or suspected perception limitations; expected to fail today and
         reported as findings, not as regressions
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from haptic_hub.config import HubConfig
from haptic_hub.sim import checks as c
from haptic_hub.sim.oracle import OracleNoise
from haptic_hub.sim.render import CameraModel
from haptic_hub.sim.world import Box, Walker, World, const, person, pole, ramp, steps, wall

ALL = (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class Fault:
    kind: str  # camera_freeze | phone_disconnect | garbage | latency | perception_crash |
    #            perception_stall | belt_drop | belt_disconnect
    start: float
    end: float
    value: float = 0.0


@dataclass
class Scenario:
    name: str
    description: str
    duration: float
    build: Callable[[], World]
    checks: list[c.Check]
    faults: list[Fault] = field(default_factory=list)
    tags: frozenset[str] = frozenset({"core"})
    camera: CameraModel = field(default_factory=CameraModel)
    noise: OracleNoise | None = None
    configure: Callable[[HubConfig], None] | None = None
    fps: float = 15.0
    danger_m: float = 1.2  # for the informational coverage metric
    quiet_m: float = 3.0


def _pole_ahead(x: float = 0.0, y: float = 1.2) -> Callable[[], World]:
    return lambda: World([pole(x, y, radius=0.05)], Walker())


def _wall_ahead(walk_s: float, speed: float = 0.8, face_y: float = 4.525, **walker) -> Callable[[], World]:
    def build() -> World:
        return World([wall(-6, face_y + 0.075, 6, face_y + 0.075)], Walker(speed=steps((0, speed), (walk_s, 0)), **walker))

    return build


def _approaching_person(speed: float = 1.5, start_y: float = 9.0, stop_y: float = 1.9, x: float = 0.0,
                        confidence: float = 0.9, extra=()) -> Callable[[], World]:  # fmt: skip
    """Standing wearer; a person walks straight at them and stops at stop_y."""
    stop_t = (start_y - stop_y) / speed

    def build() -> World:
        p = person(x, start_y, velocity=(0, -speed), moving=steps((0, 1), (stop_t, 0)), confidence=confidence)
        return World([*extra, p], Walker())

    return build


def _careful_walker(threshold: int = 150, reaction_s: float = 0.4):
    """A crude model of a person using the belt: waits while it plays the fault
    pattern (including at start-up), and stops for good reaction_s after any of
    the three middle motors reaches `threshold`."""
    state: dict[str, float | None] = {"felt": None}

    def react(levels: tuple[int, ...], fault: bool, t: float) -> float:
        if fault:
            return 0.0
        if state["felt"] is None and max(levels[1:4]) >= threshold:
            state["felt"] = t
        felt = state["felt"]
        return 0.0 if felt is not None and t - felt >= reaction_s else 1.0

    return react


def _scenarios() -> list[Scenario]:
    deg = math.radians
    s: list[Scenario] = []

    # ------------------------------------------------------------ core behavior
    s.append(Scenario(
        "approach_wall", "Walk at 0.8 m/s toward a wide wall and stop ~0.9 m short.", 6.5,
        _wall_ahead(walk_s=4.5),
        [c.motors_off(ALL, 0.8, 2.3), c.motors_on(ALL, 3.4, 6.5), c.level_rises(2, (3.0, 3.6), (5.0, 6.5)),
         c.no_fault(0.8, 6.5), c.no_collision(), c.status_reports(1.0, 6.5, healthy=True)],
    ))  # fmt: skip
    s.append(Scenario(
        "pole_left", "Standing; a thin pole 1.2 m ahead, 0.55 m to the left.", 4.0,
        _pole_ahead(-0.55), [c.motors_on(0, 1.0, 4.0), c.motors_off((1, 2, 3, 4), 1.0, 4.0), c.no_fault(0.8, 4.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "pole_right", "Standing; a thin pole 1.2 m ahead, 0.55 m to the right.", 4.0,
        _pole_ahead(0.55), [c.motors_on(4, 1.0, 4.0), c.motors_off((0, 1, 2, 3), 1.0, 4.0), c.no_fault(0.8, 4.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "two_poles", "Poles left and right with a clear gap in the middle.", 4.0,
        lambda: World([pole(-0.55, 1.2, radius=0.05), pole(0.55, 1.2, radius=0.05)], Walker()),
        [c.motors_on((0, 4), 1.0, 4.0), c.motors_off((1, 2, 3), 1.0, 4.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "pole_center", "Standing; a thin pole 1.2 m straight ahead.", 4.0,
        _pole_ahead(0.0), [c.motors_on(2, 1.0, 4.0), c.motors_off((0, 1, 3, 4), 1.0, 4.0), c.no_fault(0.8, 4.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "three_obstacles", "Pole left at 1.2 m, wall segment center-right at 1.6 m, nothing far right.", 4.0,
        lambda: World([pole(-0.55, 1.2, radius=0.05), wall(-0.05, 1.6, 0.35, 1.6, thickness=0.1)], Walker()),
        [c.motors_on((0, 2, 3), 1.0, 4.0), c.motors_off((1, 4), 1.0, 4.0), c.strongest(0, 1.0, 4.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "person_vs_pole", "Standing; a still pole 3 m ahead-right (outside the depth range) and a person "
        "walking in at 1.5 m/s ahead-left who stops 2.6 m away. Only the approaching person should buzz, "
        "and only while approaching.", 6.0,
        _approaching_person(start_y=7.5, stop_y=2.6, x=-0.5, extra=(pole(0.5, 3.0, radius=0.05),)),
        [c.motors_off(ALL, 1.0, 2.2), c.motors_on(1, 2.85, 3.35, min_frac=0.7), c.motors_off((3, 4), 1.0, 6.0),
         c.motors_off(ALL, 4.6, 6.0), c.no_fault(0.8, 6.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "camera_motion", "Standing; a still person 4 m ahead and a pole 3.5 m ahead-right. The head sways "
        "+-10 deg (yaw, 1.2 Hz) and bobs +-5 deg (pitch, 2.4 Hz). Nothing is close or approaching.", 5.0,
        lambda: World([person(0, 4.0), pole(0.6, 3.5)],
                      Walker(head_yaw=lambda t: deg(10) * math.sin(2 * math.pi * 1.2 * t),
                             pitch=lambda t: deg(5) * math.sin(2 * math.pi * 2.4 * t))),
        [c.motors_off(ALL, 1.0, 5.0), c.no_fault(0.8, 5.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "walking_sway", "Walk 0.8 m/s toward a wall with +-8 deg head sway; the wall must still alert.", 6.5,
        _wall_ahead(walk_s=4.5, head_yaw=lambda t: deg(8) * math.sin(2 * math.pi * 1.0 * t)),
        [c.motors_on((1, 2, 3), 4.0, 6.5), c.no_fault(0.8, 6.5), c.no_collision()],
    ))  # fmt: skip
    s.append(Scenario(
        "low_confidence_person", "person_approaching, but the detector only scores the person 0.2 (below "
        "detector_conf 0.35, e.g. poor light). Depth must still alert once the person is in range, and the "
        "phone's status must mark the center sector 'uncertain' the whole time (never silently nothing).", 6.0,
        _approaching_person(confidence=0.2),
        [c.motors_off(ALL, 1.0, 4.1), c.motors_on(2, 4.6, 6.0), c.no_fault(0.8, 6.0),
         c.status_uncertain(2, 1.2, 6.0), c.status_uncertain((0, 4), 1.2, 6.0, expected=False),
         c.status_uncertain((1, 3), 1.2, 3.5, expected=False)],
    ))  # fmt: skip
    s.append(Scenario(
        "head_turn", "Pole straight ahead of the torso; at 2.5 s the wearer looks 25 deg left. "
        "The alert follows the head (documented behavior), so it moves to the right motor.", 5.0,
        lambda: World([pole(0, 1.5, radius=0.05)], Walker(head_yaw=ramp(2.5, 2.8, 0.0, deg(-25)))),
        [c.motors_on(2, 1.0, 2.4), c.motors_off((0, 1, 3, 4), 1.0, 2.4),
         c.motors_on(4, 3.2, 5.0), c.motors_off((0, 1, 2), 3.2, 5.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "person_approaching", "Standing; a person walks at the wearer at 1.5 m/s from 9 m and stops 1.9 m "
        "away. Box-growth TTC must alert before depth range (~2.27 m, from t~4.3 s).", 6.0,
        _approaching_person(),
        [c.motors_off(ALL, 1.0, 3.2), c.motors_on(2, 3.85, 4.3, min_frac=0.8), c.motors_on(2, 4.4, 6.0),
         c.no_fault(0.8, 6.0), c.status_uncertain(ALL, 1.2, 6.0, expected=False)],
        danger_m=1.8,
    ))  # fmt: skip
    s.append(Scenario(
        "clear_path", "Walk 1 m/s down an open path lined with poles 3.5 m to each side.", 5.0,
        lambda: World([pole(x, y) for y in range(2, 20, 3) for x in (-3.5, 3.5)] + [wall(-10, 30, 10, 30)],
                      Walker(speed=const(1.0))),
        [c.motors_off(ALL, 1.0, 5.0), c.no_fault(0.8, 5.0), c.no_collision()],
    ))  # fmt: skip
    s.append(Scenario(
        "careful_walker", "Closed loop: the wearer walks at 1.2 m/s toward a wall and stops 0.4 s after a middle "
        "motor reaches level 150.", 7.0,
        lambda: World([wall(-6, 6.6, 6, 6.6)], Walker(speed=const(1.2), react=_careful_walker())),
        [c.no_collision(), c.clearance_at_end(0.3, 1.6), c.motors_on(2, 6.0, 7.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "noisy_depth", "approach_wall with a depth model that reads 15% far and has 10% per-pixel noise.", 6.5,
        _wall_ahead(walk_s=4.5), [c.motors_on(ALL, 4.5, 6.5), c.no_fault(0.8, 6.5), c.no_collision()],
        noise=OracleNoise(depth_scale=1.15, depth_sigma=0.10),
    ))  # fmt: skip
    s.append(Scenario(
        "flaky_detector", "person_approaching with a detector that misses the person on 30% of frames.", 6.0,
        _approaching_person(),
        [c.motors_off(ALL, 1.0, 3.2), c.motors_on(2, 4.4, 6.0, min_frac=0.85), c.no_fault(0.8, 6.0)],
        noise=OracleNoise(miss_prob=0.3),
    ))  # fmt: skip

    # ------------------------------------------------------------ geometry (floor-relative depth)
    def room(ceiling: float) -> Callable[[], World]:
        return lambda: World([wall(-4, 6, 4, 6), wall(-4, -6, 4, -6), wall(-4, -6, -4, 6), wall(4, -6, 4, 6)],
                             Walker(), ceiling=ceiling)  # fmt: skip

    def low_box() -> Box:
        return Box(0.45, (40, 140, 200), center=(0, 3.3), size=(1.2, 0.6), name="low box")

    s.append(Scenario(
        "indoor_ceiling", "Standing in the middle of an 8 x 12 m room with a 2.6 m ceiling; nearest wall "
        "is 6 m ahead. The ceiling must not alert.", 4.0,
        room(2.6), [c.motors_off(ALL, 1.0, 4.0), c.no_fault(0.8, 4.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "low_ceiling", "indoor_ceiling with a 2.1 m ceiling (0.5 m above the eyes): still above head "
        "height, so still quiet.", 4.0,
        room(2.1), [c.motors_off(ALL, 1.0, 4.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "looking_down", "Open ground, nothing within 10 m, head pitched 20 deg down (a normal walking "
        "posture). The floor must not alert.", 4.0,
        lambda: World([wall(-10, 15, 10, 15)], Walker(pitch=const(deg(20)))),
        [c.motors_off(ALL, 1.0, 4.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "looking_down_pole", "Head pitched 20 deg down, a thin pole 1.2 m straight ahead: the pole still "
        "alerts on the center motor only.", 4.0,
        lambda: World([pole(0, 1.2, radius=0.05)], Walker(pitch=const(deg(20)))),
        [c.motors_on(2, 1.0, 4.0), c.motors_off((0, 1, 3, 4), 1.0, 4.0)],
    ))  # fmt: skip
    s.append(Scenario(
        "head_bob_walk", "Walk 1 m/s on open ground with the head pitched 15 +-8 deg (2 Hz bob) and "
        "+-6 deg yaw sway. Nothing is within 10 m: quiet.", 5.0,
        lambda: World([wall(-10, 30, 10, 30)],
                      Walker(speed=const(1.0), pitch=lambda t: deg(15) + deg(8) * math.sin(2 * math.pi * 2 * t),
                             head_yaw=lambda t: deg(6) * math.sin(2 * math.pi * 0.9 * t))),
        [c.motors_off(ALL, 1.0, 5.0), c.no_collision()],
    ))  # fmt: skip
    s.append(Scenario(
        "noisy_depth_looking_down", "looking_down (15 deg) with a depth model that reads 15% far and has "
        "10% per-pixel noise: the fitted floor absorbs the scale error, the floor stays quiet.", 4.0,
        lambda: World([wall(-10, 15, 10, 15)], Walker(pitch=const(deg(15)))),
        [c.motors_off(ALL, 1.0, 4.0)],
        noise=OracleNoise(depth_scale=1.15, depth_sigma=0.10),
    ))  # fmt: skip
    s.append(Scenario(
        "low_obstacle", "Walk 0.8 m/s toward a 0.45 m tall box (bench / bin height) whose front is 3 m "
        "ahead. It must alert on the middle motors while it is in view (about 2.2 m down to 1.1 m, when "
        "it drops below the assumed field of view).", 3.0,
        lambda: World([low_box()], Walker(speed=const(0.8))),
        [c.motors_off(ALL, 0.5, 0.75), c.motors_on((1, 2, 3), 1.3, 2.2)],
    ))  # fmt: skip
    s.append(Scenario(
        "low_obstacle_careful", "Closed loop: the careful walker (stops 0.4 s after a middle motor reaches "
        "150) walks at 0.8 m/s toward the 0.45 m box. No collision.", 5.0,
        lambda: World([low_box()], Walker(speed=const(0.8), react=_careful_walker())),
        [c.no_collision(), c.clearance_at_end(0.3)],
    ))  # fmt: skip
    s.append(Scenario(
        "ttc_on_time", "Person walks in at 1.2 m/s from 8 m. The box-growth TTC alert must be on by the "
        "time the true TTC (center distance / speed) is 2.5 s (t~4.2 s), and quiet while it is > 4 s.", 6.0,
        _approaching_person(speed=1.2, start_y=8.0),
        [c.motors_off(ALL, 1.0, 2.0), c.motors_on(2, 4.2, 4.5, min_frac=0.9)],
    ))  # fmt: skip
    s.append(Scenario(
        "jittery_boxes", "Standing; a person walks in at 1.5 m/s from 9 m, detector boxes jitter 4 px "
        "(sigma per edge, full-res). Quiet while the true front-surface TTC is > 3.3 s (t < 2.5 s); the "
        "alert still comes once the person is close.", 6.0,
        _approaching_person(), [c.motors_off(ALL, 1.0, 2.5), c.motors_on(2, 4.4, 6.0, min_frac=0.85)],
        noise=OracleNoise(box_jitter_px=4.0),
    ))  # fmt: skip

    for true_fov in (50.0, 70.0):
        s.append(Scenario(
            f"fov_{true_fov:.0f}_looking_down", f"looking_down, but the glasses' real horizontal FOV is "
            f"{true_fov:.0f} deg while the hub assumes 60 (it is unmeasured): the floor fit must tolerate it.", 4.0,
            lambda: World([wall(-10, 15, 10, 15)], Walker(pitch=const(deg(20)))),
            [c.motors_off(ALL, 1.0, 4.0)], camera=CameraModel(hfov_deg=true_fov),
            configure=lambda cfg: setattr(cfg.perception, "hfov_deg", 60.0),
        ))  # fmt: skip
        s.append(Scenario(
            f"fov_{true_fov:.0f}_pole", f"pole_center, but the real horizontal FOV is {true_fov:.0f} deg "
            "while the hub assumes 60.", 4.0,
            _pole_ahead(0.0), [c.motors_on(2, 1.0, 4.0), c.motors_off((0, 1, 3, 4), 1.0, 4.0)],
            camera=CameraModel(hfov_deg=true_fov), configure=lambda cfg: setattr(cfg.perception, "hfov_deg", 60.0),
        ))  # fmt: skip

    # ------------------------------------------------------------ failure injection
    fault = frozenset({"fault"})
    stale = HubConfig().safety.frame_stale_s
    belt_period = 1 / HubConfig().haptics.send_hz
    s.append(Scenario(
        "camera_freeze", "Pole ahead; the glasses stream stalls for 2 s (phone still connected).", 6.5,
        _pole_ahead(),
        [c.motors_on(2, 1.0, 2.4), c.fault_within(2.5, stale + belt_period + 0.15), c.fault_during(3.7, 4.5),
         c.recovers_within(4.5, 0.6), c.motors_on(2, 5.3, 6.5), c.status_reports(3.8, 4.5, healthy=False)],
        [Fault("camera_freeze", 2.5, 4.5)], fault,
    ))  # fmt: skip
    s.append(Scenario(
        "phone_disconnect", "Pole ahead; the phone's WebSocket drops for 2 s, then reconnects.", 6.5,
        _pole_ahead(),
        [c.motors_on(2, 1.0, 2.4), c.fault_within(2.5, stale + belt_period + 0.15), c.fault_during(3.7, 4.5),
         c.recovers_within(4.5, 0.8), c.motors_on(2, 5.5, 6.5), c.counter("phone_connects", lambda n: n >= 2, ">= 2")],
        [Fault("phone_disconnect", 2.5, 4.5)], fault,
    ))  # fmt: skip
    s.append(Scenario(
        "perception_crash", "Pole ahead; the model raises on every frame for 1.5 s.", 5.5,
        _pole_ahead(),
        [c.motors_on(2, 1.0, 2.4), c.fault_within(2.5, 0.3), c.fault_during(2.9, 4.0), c.recovers_within(4.0, 0.6),
         c.motors_on(2, 4.8, 5.5), c.status_reports(2.9, 4.0, healthy=False)],
        [Fault("perception_crash", 2.5, 4.0)], fault,
    ))  # fmt: skip
    result_stale = HubConfig().safety.result_stale_s
    s.append(Scenario(
        "perception_stall", "Pole ahead; one inference hangs for 2.5 s (frames keep arriving).", 6.5,
        _pole_ahead(),
        [c.motors_on(2, 1.0, 2.4), c.fault_within(2.5, result_stale + 0.3), c.recovers_within(5.0, 0.6),
         c.motors_on(2, 5.8, 6.5)],
        [Fault("perception_stall", 2.5, 2.6, value=2.5)], fault,
    ))  # fmt: skip
    # Lost writes: the hub writes with response, so it notices within one command
    # TTL (+ a belt-loop tick, + one status period for the phone to hear it), sends
    # the fault pattern, and after BleBelt.WRITE_TIMEOUT_S without an answer drops
    # the connection and reconnects.
    ttl_s = HubConfig().haptics.ttl_ms / 1000
    status_period = 1 / HubConfig().status_hz
    s.append(Scenario(
        "belt_writes_lost", "Pole ahead; BLE writes are silently lost for 1 s (link stays up). The firmware TTL "
        "plays the fault pattern; the hub must report 'belt not acknowledging', reconnect, and recover.", 5.5,
        _pole_ahead(),
        [c.motors_on(2, 1.0, 2.4), c.fault_within(2.5, ttl_s + belt_period + 0.05), c.fault_during(2.9, 3.5),
         c.status_unhealthy_within(2.5, ttl_s + belt_period + status_period + 0.15, "belt not acknowledging"),
         c.status_reports(2.5 + ttl_s + belt_period + status_period + 0.15, 3.5, healthy=False, min_frac=1.0),
         c.recovers_within(3.5, 0.6), c.motors_on(2, 4.3, 5.5), c.status_reports(4.5, 5.5, healthy=True, min_frac=1.0),
         c.counter("belt_reconnects", lambda n: n >= 1, ">= 1")],
        [Fault("belt_drop", 2.5, 3.5)], fault,
    ))  # fmt: skip
    s.append(Scenario(
        "belt_disconnect", "Pole ahead; the BLE link drops for 1 s; the hub reconnects.", 5.5,
        _pole_ahead(),
        [c.motors_on(2, 1.0, 2.4), c.fault_within(2.5, 0.05), c.fault_during(2.6, 3.5),
         c.status_unhealthy_within(2.5, belt_period + status_period + 0.1),
         c.status_reports(2.5 + belt_period + status_period + 0.1, 3.5, healthy=False, min_frac=1.0),
         c.recovers_within(3.5, 0.8), c.motors_on(2, 4.5, 5.5), c.status_reports(4.7, 5.5, healthy=True, min_frac=1.0)],
        [Fault("belt_disconnect", 2.5, 3.5)], fault,
    ))  # fmt: skip
    s.append(Scenario(
        "garbage_frames", "Pole ahead; the phone interleaves malformed packets and broken JPEGs.", 5.0,
        _pole_ahead(),
        [c.no_fault(0.8, 5.0), c.motors_on(2, 1.0, 5.0), c.counter("bad_messages", lambda n: n >= 10, ">= 10")],
        [Fault("garbage", 0.5, 5.0, value=2)], fault,
    ))  # fmt: skip
    s.append(Scenario(
        "network_latency", "approach_wall with 200 +-100 ms phone->hub latency.", 6.5,
        _wall_ahead(walk_s=4.5),
        [c.no_fault(0.8, 6.5), c.motors_on(ALL, 4.2, 6.5), c.no_collision()],
        [Fault("latency", 0.0, 6.5, value=0.2)], fault,
    ))  # fmt: skip

    # ------------------------------------------------------------ probes (limitations)
    probe = frozenset({"probe"})
    s.append(Scenario(
        "probe_low_obstacle", "Walk at 0.8 m/s toward a 0.45 m tall box 3 m ahead, never stopping. Once "
        "it is closer than ~1.1 m it is below the (assumed 60 deg H / 92 deg V) field of view and nothing "
        "remembers it: a wearer who hasn't stopped by then walks into it.", 5.5,
        lambda: World([Box(0.45, (40, 140, 200), center=(0, 3.3), size=(1.2, 0.6), name="low box")],
                      Walker(speed=const(0.8))),
        [c.motors_on(2, 2.6, 3.4, min_frac=0.5), c.no_collision()], tags=probe,
    ))  # fmt: skip
    s.append(Scenario(
        "probe_low_confidence_warning", "low_confidence_person, asking for the early (TTC) warning that a "
        "confident detection gives at 3.85-4.3 s. By design uncertain boxes are only reported (status "
        "sectors[i].uncertain), they don't drive the belt: does the wearer feel anything before depth range?", 6.0,
        _approaching_person(confidence=0.2),
        [c.motors_on(2, 3.85, 4.3, min_frac=0.5)], tags=probe,
    ))  # fmt: skip
    return s


SCENARIOS: dict[str, Scenario] = {sc.name: sc for sc in _scenarios()}


def select(names: list[str] | None, tags: set[str] | None = None) -> list[Scenario]:
    if names:
        unknown = [n for n in names if n not in SCENARIOS]
        if unknown:
            raise KeyError(f"unknown scenario(s) {unknown}; known: {sorted(SCENARIOS)}")
        return [SCENARIOS[n] for n in names]
    return [sc for sc in SCENARIOS.values() if tags is None or sc.tags & tags]
