# hub/haptic_hub/sim: simulated world

Tests the whole chain without glasses, phone, or belt. **Every result here is a
simulation.** It shows that the software pieces fit together and handle
failures. It does not show that the system works on a real walk.

```text
World (ray-cast, numpy) --> SimPhone --HB frames over WebSocket--> GlassesServer   (real)
  ^   glasses camera view      504x896 JPEG q60, 15 fps                |
  |                                                    Hub: perception -> ClassifiedPerception(local)
  |                                                         -> HapticPolicy + SafetyMonitor   (real)
  |                                                                    |
  +-- (careful_walker) the wearer reacts <-- EmulatedBelt <-- encode_belt_command bytes
                                        esp32/lib/belt_core built for the host (real logic)
```

| Real (production code) | Simulated |
|---|---|
| WebSocket server and `HB` frame decoding, `Hub` loops, `VisionPerception` fusion (depth bands, percentiles, box-growth TTC), `ClassifiedPerception`, `HapticPolicy`, `SafetyMonitor`, belt wire encoding | camera images (simple flat-shaded renders) |
| belt firmware decoding plus TTL/fault state machine (`esp32/lib/belt_core`, compiled with the host C++ compiler through `belt_capi.cpp`); the hub's `BleBelt` writer, acknowledgement window and reconnects | the phone app, Wi-Fi, BLE radio (`SimRadioClient`), PWM/motors, the boot self-test |
| | the models, when `--perception oracle` (default): ground-truth depth and boxes replace Depth Anything and YOLO |

## Run

```bash
cd hub
uv run python -m haptic_hub.sim                 # core + fault scenarios (~2.5 min); exit 1 on any failure
uv run python -m haptic_hub.sim --probes        # + known-limitation probes (reported, never fail the run)
uv run python -m haptic_hub.sim --list
uv run python -m haptic_hub.sim pole_left head_turn --show          # live window
uv run python -m haptic_hub.sim --record sim-runs/ --json sim-runs/report.json
uv run python -m haptic_hub.sim --perception vision                 # real models on renders (needs --extra vision)
uv run python -m haptic_hub.sim --motion flow                       # + optical-flow motion, as `haptic-hub run --motion flow`
uv run pytest tests/test_sim.py                 # unit tests + 4 fast scenarios
HUB_SIM_ALL=1 uv run pytest tests/test_sim.py   # every gating scenario
```

It needs a C++17 compiler (`c++`, `clang++`, `g++`, or `$CXX`). The firmware
library is built once into `hub/.sim-build/` (gitignored). The sim adds no
Python dependencies.

Scenarios run in real time, because the hub's loops use `time.monotonic()` and
asyncio sleeps. The `--perception vision|depth|detector` results are for
information only. YOLO does not recognize the flat-shaded "person" cylinder,
and these renders say nothing about accuracy on real glasses video.

Perception is assembled the way `haptic-hub run` does it:
- `build_perception` (or the oracle);
- `FlowMotionPerception` when `--motion flow` is set;
- `ClassifiedPerception` with the local backend (the sim never calls the cloud).

Each report prints the chain it ran, e.g. `oracle+flow+local-classifier`. It
also prints the hub's own stage timings (`haptic_hub.latency`) for the whole
scenario. These are simulation timings on this laptop: the phone is in the same
process, and "network" delay is whatever a scenario injects.

## What is checked

Checks look at the belt firmware's output, which is what the wearer would feel:
- `motors_on` / `motors_off` / `strongest` / `level_rises`. "Off" means no alert,
  not a clear path. The fault pattern counts as buzzing, never as quiet.
- `fault_within` / `fault_during` / `recovers_within` cover the fault pattern after injected failures.
- `status_reports` checks the health the phone is told; `status_uncertain` checks
  the per-sector `uncertain` flag the phone is told (docs/protocol.md).
- `no_collision` / `clearance_at_end` apply to the closed-loop walker.
- Three **invariants** run in every scenario:
  - whenever the hub is unhealthy, the belt plays the fault pattern within 150 ms;
  - whenever the belt has played the fault pattern for 350 ms (one TTL plus slack), the hub is not reporting healthy;
  - after start-up the belt is never idle or dormant.

The belt side runs the hub's real `BleBelt` (write-with-response, acknowledgement
window, reconnects) over `SimRadioClient`, a fake bleak client that feeds the
firmware logic. Writes are acknowledged after 20 ms, a connect takes 50 ms, and
reconnects are retried after 0.2–0.4 s (the real hub waits 1 s, then doubles).
These are assumptions, not radio measurements.

| Scenario | Covers |
|---|---|
| `pole_left`, `pole_center`, `pole_right` | direction: one motor, the others quiet |
| `two_poles`, `three_obstacles` | several obstacles at once, strongest = nearest |
| `approach_wall`, `careful_walker` | intensity grows with proximity; closed loop: a walker who stops on alerts doesn't collide |
| `person_approaching`, `person_vs_pole` | box-growth TTC alerts on an approaching person before depth range; a still pole at 3 m and a person who has stopped stay quiet |
| `head_turn`, `camera_motion`, `walking_sway` | alerts follow the head (documented); head sway/bob on still objects doesn't alert |
| `low_confidence_person`, `flaky_detector`, `noisy_depth` | detections below `detector_conf` (depth still alerts; status marks the sector `uncertain`), 30% missed detections, depth biased 15% plus noise |
| `indoor_ceiling`, `low_ceiling` | a 2.6 m / 2.1 m ceiling doesn't alert (floor-relative depth) |
| `looking_down`, `looking_down_pole`, `head_bob_walk`, `noisy_depth_looking_down` | head pitched 15–20° down, or bobbing 7–23°: the floor doesn't alert, a pole still does; also with depth 15% off plus noise |
| `fov_50_*`, `fov_70_*` | the real FOV is 50° or 70° while the hub assumes 60°: floor stays quiet, a pole still alerts on the right motor |
| `low_obstacle`, `low_obstacle_careful` | a 0.45 m box alerts while in view; a walker who stops on the alert doesn't hit it |
| `ttc_on_time`, `jittery_boxes` | box-growth TTC alert is on by true TTC 2.5 s; 4 px box jitter doesn't alert on a person > 5 m away |
| `camera_freeze`, `phone_disconnect` | stale or missing frames: fault after `frame_stale_s`, then recovery |
| `perception_crash`, `perception_stall` | model exceptions and hangs: fault, then recovery |
| `belt_writes_lost`, `belt_disconnect` | the hub link goes quiet: the **firmware** TTL/link-lost logic plays the fault pattern, the phone is told `belt not acknowledging` / `belt disconnected` within one TTL + one status period, and the hub reconnects |
| `garbage_frames`, `network_latency` | malformed packets and broken JPEGs are rejected without a fault; 200 ±100 ms latency |

Each check was confirmed to fail on a deliberately broken hub:
- left/right swapped in the policy: caught by `pole_left` and `head_turn`;
- a safety monitor that always reports healthy: caught by `camera_freeze` and `perception_stall`;
- a 1000 ms belt TTL: caught by `belt_writes_lost`.

## Findings from the probes (simulation, oracle perception, default config)

Under ideal (ground-truth) perception, these show up as properties of the current
thresholds and geometry. Real models may make them better or worse.

Fixed on 2026-11-08 (`chxrro/perception-fixes`) and now gating: the ceiling and
looking-down false alerts (floor-relative depth), the missed low box while it is
in view, the late TTC alert and the TTC alerts from box jitter (1/height fit with
fit-quality gating). Low-confidence detections are now reported per sector
(`uncertain`). The scenarios above check all of this.

Still open (probes, reported, never fail the run):

- `probe_low_obstacle`: the 0.45 m box is alerted from about 2.2 m. Once it is
  closer than about 1.1 m it is below the bottom of the frame (60° H / 92° V FOV
  assumed, head level). Nothing remembers it, so a walker who never stops still
  hits it. `low_obstacle_careful` shows a walker who reacts to the alert stops in
  time.
- `probe_low_confidence_warning`: a person scored below `detector_conf` gets no
  early belt warning. By design, uncertain boxes are only reported
  (`sectors[i].uncertain` in the status) and don't drive the belt. Depth still
  alerts once the person is in range. Whether uncertain objects should get a weak
  vibration is a team decision.

Real models on these renders (`--perception vision`, informational, Apple MPS,
2026-11-08): `pole_left/center/right`, `approach_wall`, `clear_path`,
`camera_freeze`, `indoor_ceiling`, `looking_down` and `looking_down_pole` pass,
as on the old band geometry. `head_bob_walk` buzzes 21% of the time (the band
geometry passed it). Depth Anything's floor on these flat renders is bent: it reads
20–60% too far near the bottom of the frame. On some frames that passes the
trust check but gives a tilted floor, so the side of the floor reads as obstacles.
`low_obstacle` is alerted 22% of its window (band: 0%). `person_approaching` still
gets no TTC alert because YOLO doesn't see the cylinder as a person. Whether real
glasses video behaves like the renders here is unknown. Replay recorded clips
with `--depth-geometry floor` and `band` before relying on either.

The hub also reports `healthy` while BLE writes are being lost. Write-without-response
gives no feedback, and only the belt's TTL catches it (`belt_writes_lost`).

## Assumptions (not measured)

- Camera: 504×896 portrait at 15 fps, JPEG quality 60, as `meta/` requests.
  The **60° horizontal FOV** assumes about 100° diagonal and has not been measured
  on the glasses. The hub's floor fit uses the same assumed FOV (`hfov_deg`); the
  `fov_50_*` / `fov_70_*` scenarios render with a different true FOV to check
  that a wrong assumption of ±10° is tolerated.
- Eye height is 1.6 m. The camera sits at eye level with no offset. Obstacles are
  vertical prisms on flat ground.
- The emulated belt reconnects instantly. The real hub's BLE reconnect backs off for 1 s or more.

## Adding a scenario

Add a `Scenario(...)` in `scenarios.py`: a world factory, a duration, checks, and
optional `faults` (`camera_freeze`, `phone_disconnect`, `garbage`, `latency`,
`perception_crash`, `perception_stall`, `belt_drop`, `belt_disconnect`). Tag
it `probe` if it documents a limitation instead of required behavior. Pick check
windows from the scenario geometry, with some slack for the 15 fps pipeline.
