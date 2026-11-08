# Depth, collision-risk, and belt design investigation

> **The team's belt contract is [docs/protocol.md](protocol.md).** It fixes a
> 5-motor front belt; vibration = obstacle in that direction relative to the
> glasses (head), not "move this way"; intensity = urgency (closer or sooner); a
> distinct fault pattern (double pulse on all motors every 2 s) when the system
> can't vouch for its output, including BLE disconnect and command timeout. This
> research note predates that contract. Where it differs (undecided motor count,
> body-relative regions, cadence-coded urgency, all-motors-off on disconnect),
> protocol.md wins; change it only through that document.

Research checked November 7, 2026. The live pipeline now uses NVIDIA detections
and measured optical flow for experimental collision candidates; see
[the current collision check](collision-check.md). A local CPU replay baseline now implements
NanoDet object boxes, class-aware IoU tracking, and known-size ArUco target pose.
See [the recording/perception guide](session-recording.md) for commands and limits.
The general depth, collision and haptics designs below remain proposals. No depth model,
tracker, belt firmware, or hardware command transport has been installed or run
as part of this investigation. No new model training or GPU allocation is required
to begin the evaluation below.

## Recommended next experiment

Evaluate **Depth Anything V2 Metric Hypersim Small** on a few stationary indoor
scenes with measured distances. Compare **Depth Pro** on the same scenes if the
small model has a specific failure or unacceptable scale error. Keep Cosmos3-Edge
for semantic observations and explanation. Keep numeric geometry, risk policy,
and haptic scheduling separate from the LLM's free-form text.

This is an evaluation order, not a claim that either estimator is accurate enough
for navigation. We have not measured either model's speed, VRAM use, or accuracy
on the MacBook, A10G, or Ray-Ban images. Do not budget GPU memory from parameter
count alone: the existing vLLM service reserves 85% of GPU memory. Start a separate
benchmark or stop vLLM during a future depth-only test before assuming coexistence.

## Pretrained distance-estimation candidates

| Candidate | What the official implementation provides | What to check for this project |
|---|---|---|
| [Depth Anything V2 metric models](https://github.com/DepthAnything/Depth-Anything-V2/tree/main/metric_depth) | Pretrained indoor Hypersim and outdoor Virtual KITTI metric checkpoints; Small is listed as 24.8M parameters. Its documented output is a depth map in meters. | Use an explicit **metric** checkpoint; a relative-depth visualization is not a meter measurement. Indoor/outdoor domain choice matters. Verify checkpoint-specific license, scale error, floor boundaries, thin objects, and actual runtime. |
| [Depth Pro](https://github.com/apple/ml-depth-pro) | Pretrained monocular metric depth, predicted focal length, and optional supplied focal length. The authors say it can estimate absolute scale without camera intrinsics. | Test errors even when meters are returned. The reference weights differ from the paper's model; published timing is not our benchmark. Code and weights have [Apple's custom license](https://github.com/apple/ml-depth-pro/blob/main/LICENSE). |
| [Metric3D / Metric3Dv2](https://github.com/YvanYin/Metric3D) | Pretrained metric depth and, for v2, surface normals. The documented camera normalization and recovery use focal length. | Obtain camera intrinsics for the **resized/cropped** input; the project specifically warns that the wrong focal length distorts recovered geometry. Check code and weights' terms separately. |

An estimator returning meters does not establish a known error bound on our
camera. Calibration, validation, and training are different tasks. Begin with
calibration/validation of pretrained weights; consider fine-tuning only after
recording a repeatable failure and suitable training/evaluation data.

For each frame retain timestamp, dimensions, orientation, and the transform from
original camera pixels to model pixels. Verify whether the chosen depth output
means camera-axis Z depth or distance along a viewing ray before converting to a
3D point. Camera metadata is not automatically preserved through JPEG submission.

Select object regions with masks or cautiously inset boxes, then compare robust
near-surface depth statistics against reference measurements. A box may contain
background, floor, or other objects; its center pixel is not necessarily the
object's distance. Treat unsupported reflective/transparent surfaces, missing
measurements, floor/drop-off geometry, occlusion, and unstable scale as unknown.
Do not turn a model confidence score into a probability of safe navigation.

## Collision-risk software and geometry

| Component to investigate | Useful role | What it does not establish |
|---|---|---|
| [OpenCV optical flow](https://docs.opencv.org/4.x/d4/dee/tutorial_optical_flow.html) | Measure image motion, compare frame pairs, and investigate visual expansion/looming. | Motion is affected by camera/head rotation, translation, lighting, and occlusion. Flow is not metric range or an independent proof of collision. |
| [ByteTrack](https://github.com/ifzhang/ByteTrack) | Associate structured detector boxes across frames to maintain candidate object tracks. | Needs actual detector boxes/scores. It does not produce depth, body motion, or collision decisions. Its tracking results are not evidence for our camera. |
| [Nav2 Collision Monitor / Detector](https://github.com/ros-navigation/navigation2/tree/main/nav2_collision_monitor) | Reference implementation of geometric detection zones, temporal debounce, and time-to-collision behavior. The Detector reports occupied zones without controlling motion. | These ROS components expect inputs such as scans/point clouds/ranges and coordinate transforms. The Monitor's approach logic uses robot velocity. They cannot accept an LLM paragraph as validated geometry or be directly equated to a moving human with a head-mounted camera. |

Start with a small deterministic zone/track policy after depth is validated,
rather than adding an entire ROS stack solely for this hackathon. The initial replay experiment uses OpenCV Zoo NanoDet with a small IoU tracker;
the validated depth and collision policy described here remain proposals.

Proposed processing order:

```text
Timestamped camera frame + camera calibration/orientation
  -> detector/masks + validated depth estimate
  -> association with prior observations + quality checks
  -> transform into wearer/body coordinates
  -> swept-body corridor / height bands and relative approach checks
  -> explicit risk/unknown state
  -> bounded haptic policy
```

Camera-image left and body-left differ when the wearer turns their head. A
MacBook's fixed view cannot validate the Ray-Ban-to-belt mapping. Either measure
head-to-body orientation or explicitly restrict the first supervised experiment
to a fixed, forward-facing head pose; invalidate alerts when this assumption is
violated. Do not infer heading from an object's image position.

For one tracked object, a first diagnostic is radial closing rate and radial TTC:

```text
closing_rate_mps = (previous_range_m - current_range_m) / elapsed_seconds
radial_ttc_s = current_range_m / closing_rate_mps  # only if closing is established
```

Use actual capture timestamps, matched tracks, validated comparable ranges,
several samples/smoothing, and explicit uncertainty. Nonpositive, noisy, or
unavailable closing rate means **no TTC estimate**, not infinite safety. Radial
TTC assumes continued approach on the viewing ray; lateral passing, body size,
head motion, and changing speed can invalidate it. Full collision checks also
need projected closest approach and a wearer corridor. A stationary close
obstacle may still need an alert without a usable TTC estimate.

Do not choose distance/TTC thresholds from an LLM answer. Derive candidate
thresholds from measured total latency, observed motion, and a controlled demo's
stopping margin, then evaluate false positives/misses. For illustration only,
a wearer moving 1 m/s travels 1 m during a one-second delay. A 1 Hz semantic loop
has not been validated as a sufficient collision-alert update rate.

The capture loop's three-second freshness label is a developer setting. It is
**not** permission to drive a belt using three-second-old observations. Hardware
alert deadlines must be independently established before actuation.

## Proposed interface between perception and belt policy

This is a contract sketch, not an implemented schema or a valid current output:

```json
{
  "frame_id": 123,
  "captured_at": 1794100000.0,
  "coordinate_frame": "wearer_body",
  "health": {
    "camera": "available",
    "depth": "unvalidated",
    "orientation": "unknown",
    "belt": "disconnected"
  },
  "assessment": {
    "state": "unknown",
    "reason": "depth and camera-to-body orientation are not validated",
    "sector": null,
    "range_m": null,
    "radial_ttc_s": null
  }
}
```

Do not parse the current Cosmos observation into a numeric range or a motor
command. Validated depth/tracking adapters should populate numeric fields and
provenance (checkpoint, calibration ID, units, quality checks). Missing/stale
frames, low-quality depth, lost tracks, unknown orientation, inference errors,
and network loss must become explicit unavailable/uncertain states.

No detected obstacle is different from a verified clear route. An empty detector
result must not produce a "safe" message.

## Proposed belt meaning and bounded command logic

**Meaning:** a vibration marks an obstacle's location relative to the wearer.
A left vibration means **potential obstacle on the left**. It never means
"turn left." Motor count (5) and placement are set by [protocol.md](protocol.md);
the alternatives below were explored before that decision.

Use the fewest distinguishable patterns initially. Keep motor location for
obstacle direction and cadence for candidate urgency; calibrate comfortable
amplitude with the actual motors and wearer rather than assuming PWM is linear
in perceived intensity. Spatial discrimination and timing depend on the motor,
placement, body contact, and participant. See the [2024 torso coin-motor
study](https://pmc.ncbi.nlm.nih.gov/articles/PMC11596304/) and [2017 pattern
comparison study with blind participants](https://pmc.ncbi.nlm.nih.gov/articles/PMC5366061/).
Those studies do not validate our proposed codes or navigation safety.

| Policy input | Logical output for an initial bench simulation | Required behavior |
|---|---|---|
| Disabled or belt disconnected | No obstacle pulse; explicit disabled/disconnected health | All motors off locally; reconnect must not replay queued alerts. |
| Camera/inference/depth/orientation stale, missing, or uncertain | Stop directional alerts; report unavailable/uncertain | A separate health cue may be enabled only after users can distinguish it from obstacle cues. Silence must not imply a clear path. |
| Validated potential obstacle | Short pulse at its body-relative region | Only while the observation and command are valid; rate-limit repeats. |
| Validated higher-priority risk | More frequent bounded pulses at the same region | Escalate promptly; use hysteresis/debounce before de-escalating to reduce chatter. |
| No current alert condition | No obstacle pulse; health remains independently observable | Do not emit a "path clear" cue. |

Bench-only pattern candidates (disabled until hardware validation):

- Advisory: one 100 ms pulse, no more than once per 1.5 seconds.
- Higher priority: two 80 ms pulses with a 100 ms gap, no more than once per second.
- Distinct health cue to evaluate: three 40 ms pulses at all configured regions
  with 80 ms gaps, no more than once per three seconds. First verify that it
  cannot be mistaken for multiple obstacle directions; otherwise use a separate
  non-directional indicator and keep directional motors off on faults.

These numbers are starting points for discrimination/comfort testing, not
validated safety parameters. Check actual coin-motor spin-up/decay before
assuming a short requested pulse produces a distinguishable sensation. Prefer a
fixed calibrated comfortable amplitude initially; urgency must not bypass its
configured ceiling.

Proposed command/firmware requirements before any hardware integration:

1. A command contains a fresh session ID, increasing command ID, logical region,
   pattern, bounded amplitude, and short validity period. Define clock/transport
   age validation and measure delay; an expiry computed against an unsynchronized
   phone clock is insufficient.
2. Candidate bench caps: pattern total duration at most 500 ms, per-motor on-time
   at most 300 ms per command, and at most 30% duty cycle over a rolling 10-second
   window. Resolve caps with driver/current/temperature limits; lower limits win.
3. Both application and firmware enforce caps. The firmware has its own local
   watchdog: loss of valid refreshes ends actuation and turns every motor off,
   even if the application freezes. Decide the actual timeout from bench tests.
4. Reject duplicate, out-of-order, expired, and wrong-session commands. Do not
   queue old alerts across reconnects. Latest valid state replaces older state.
5. A manual stop takes priority over alerts. An unavailable state immediately
   invalidates old directional commands, not just future sends.
6. For several hazards, simulate a deterministic priority order first. Prefer
   highest urgency, then validated earliest approach/closest relevant hazard,
   with stable tie-breaking. Avoid rapid oscillation or simultaneous confusing
   patterns; do not suppress a materially more urgent alert for a cooldown.

Hardware details still needed: controller, motor count/placement, ERM/LRA type,
driver, supply/current limits, communication link, wearer calibration, and a
manual stop mechanism. Nothing in this investigation sends commands to a belt.

## Small evaluation sequence

1. **Geometry baseline, no belt:** place consenting/licensed test objects at
   tape-measured stationary distances and image positions. Compare estimate vs
   reference across lighting, cropping, camera movement, foreground/background
   edges, floor, and thin/overhanging obstacles. Record error and unavailable rate.
2. **Temporal replay:** test matched objects approaching, moving away, passing
   laterally, and disappearing. Inject stale timestamps, dropped frames, camera
   rotation, inconsistent depth, and disconnects. Check explicit unknown states.
3. **Haptic bench simulation:** inject synthetic validated risk states; check
   priority, direction, suppression, caps, timeout, reconnect, and manual stop.
   Do not use the current LLM paragraph to trigger a motor.
4. **Hardware bench:** verify actual motor identity, current draw, waveform,
   comfortable discrimination, and watchdog with the belt unworn first.
5. Only then consider a controlled supervised wearable trial. Ray-Ban capture,
   camera-to-body mapping, depth quality, and the full alert latency still need
   validation. This remains an experimental aid, not a mobility-aid replacement.

## Sources and limits

The primary repositories and articles above were read during this investigation.
Additional references:

- [Depth Anything V2 metric model instructions](https://github.com/DepthAnything/Depth-Anything-V2/blob/main/metric_depth/README.md)
- [Depth Pro paper](https://arxiv.org/abs/2410.02073)
- [Metric3Dv2 paper](https://arxiv.org/abs/2404.15506)
- [ByteTrack's license](https://github.com/ifzhang/ByteTrack/blob/main/LICENSE)
- [FeelSpace belt study in blind participants](https://pmc.ncbi.nlm.nih.gov/articles/PMC8587958/): cardinal-direction feedback, not evidence that our obstacle alerts prevent collisions.

No source benchmark has been presented as a measurement of this project. Model
weight/checkpoint licensing and exact integration versions need verification
before adopting a dependency. No weights were downloaded, no fine-tuning ran,
and `hackutd-reasoner` was not restarted for this investigation.
