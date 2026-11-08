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
- `status_reports` checks the health the phone is told.
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
| `low_confidence_person`, `flaky_detector`, `noisy_depth` | detections below `detector_conf`, 30% missed detections, depth biased 15% plus noise |
| `camera_freeze`, `phone_disconnect` | stale or missing frames: fault after `frame_stale_s`, then recovery |
| `user_stop`, `phone_vanishes`, `user_stop_unpaired` | the wearer's Stop (paired `end_session`): the session-ended cue (three pulses on motors 1+3), then quiet with status `stopped by wearer`, also while the phone's Wi-Fi drops; Start brings alerts back. A phone that vanishes without `end_session`, or an unpaired one that sends it, gets the fault pattern for as long as the hub runs |
| `perception_crash`, `perception_stall` | model exceptions and hangs: fault, then recovery |
| `belt_writes_lost`, `belt_disconnect` | the hub link goes quiet: the **firmware** TTL/link-lost logic plays the fault pattern, the phone is told `belt not acknowledging` / `belt disconnected` within one TTL + one status period, and the hub reconnects |
| `garbage_frames`, `network_latency` | malformed packets and broken JPEGs are rejected without a fault; 200 ±100 ms latency |

Each check was confirmed to fail on a deliberately broken hub:
- left/right swapped in the policy: caught by `pole_left` and `head_turn`;
- a safety monitor that always reports healthy: caught by `camera_freeze` and `perception_stall`;
- a 1000 ms belt TTL: caught by `belt_writes_lost`.
- a hub that ignores `end_session`: caught by `user_stop` (fault pattern, no cue, wrong status).

## Findings from the probes (simulation, oracle perception, default config)

Under ideal (ground-truth) perception, these show up as properties of the current
thresholds and geometry. Real models may make them better or worse.

- `probe_indoor_ceiling`: all five motors buzz in an 8×12 m room with a 2.6 m
  ceiling, with the nearest wall 6 m away. The top of the depth band (20% of image
  height) sees the ceiling about 1.6 m away.
- `probe_looking_down`: all five motors buzz in open ground with the head pitched
  20° down. The bottom of the depth band sees the floor within 2.5 m. Lowering the
  head by about 15° or more is enough.
- `probe_low_obstacle`: a 0.45 m box is never alerted and the walker collides.
  Once it is close, it sits below the depth band.
- `probe_low_confidence_warning`: a person the detector scores below 0.35 gets no
  early TTC warning and no "uncertain" signal. Only depth range alerts.
- `probe_ttc_lag`: box-growth TTC reads about 0.35–0.4 s late, roughly half the
  0.6 s fit window. At 1.2 m/s the TTC alert comes only about 0.2 s before
  depth range.
- `probe_jittery_boxes` (intermittent): 4 px box jitter sometimes makes the TTC
  path alert on a person 5–7 m away. With only 3 samples needed, the slope fit is noisy.

The hub also reports `healthy` while BLE writes are being lost. Write-without-response
gives no feedback, and only the belt's TTL catches it (`belt_writes_lost`).

## Assumptions (not measured)

- Camera: 504×896 portrait at 15 fps, JPEG quality 60, as `meta/` requests.
  The **60° horizontal FOV** assumes about 100° diagonal and has not been measured
  on the glasses. The probe results depend on it.
- Eye height is 1.6 m. The camera sits at eye level with no offset. Obstacles are
  vertical prisms on flat ground.
- The emulated belt reconnects instantly. The real hub's BLE reconnect backs off for 1 s or more.

## Adding a scenario

Add a `Scenario(...)` in `scenarios.py`: a world factory, a duration, checks, and
optional `faults` (`camera_freeze`, `phone_disconnect`, `garbage`, `latency`,
`perception_crash`, `perception_stall`, `belt_drop`, `belt_disconnect`,
`user_stop`, `user_stop_unpaired`). Tag
it `probe` if it documents a limitation instead of required behavior. Pick check
windows from the scenario geometry, with some slack for the 15 fps pipeline.
