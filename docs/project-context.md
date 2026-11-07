# Project context: HackUTD 2026

Planning context recorded on 2026-11-07. These notes capture the project owner's intended direction, not a claim that the hardware or software works.

## Goal and intended users

Build an accessibility prototype for blind users that turns visual collision risks into tactile alerts. The proposed experience uses Ray-Ban camera video and a vibration belt so the wearer can receive obstacle alerts without depending on a screen.

## Confirmed project direction

- Use Ray-Ban video as the intended visual input.
- Detect obstacles or collision risks and communicate alerts through a vibration belt.
- Explore whether fine-tuning an existing vision model would help.
- Explore NVIDIA credits or rented GPU compute.

Fine-tuning, GPU use, and a particular model are not settled implementation choices. No purchase, rental, or account setup is implied by these notes.

## Repository state at context setup

The inspected `main` branch at commit `837d79f65b7049e3f64676a19379698e5ab98c93` contained:
- `README.md`: project title, team, and clone instructions.
- `.gitignore`: common OS/editor, environment, Node, and Python exclusions.

No application code, camera integration, model, dataset, belt firmware, dependency manifest, tests, CI workflow, or executable demo was present in that snapshot. The ignore rules do not establish a chosen language or stack.

Read the current tree before using this snapshot as a description of today's implementation. Update this section as real components land.

## Conceptual pipeline, to validate

```text
Ray-Ban camera video
    -> supported capture/bridge path
    -> perception model
    -> collision-risk and haptic policy
    -> belt communication
    -> vibration actuators
```

A phone bridge is a possible part of the capture path. The phone OS, streaming method, compute location, belt controller, and communications protocol are unresolved.

Perception and alert policy are different tasks. A model may detect an object without proving it is on a collision course or measuring its distance. Do not infer reliable metric depth from a single image or bounding-box size without validation.

## Open decisions

Resolve the questions needed for the next experiment rather than choosing an entire stack in advance.

- **Camera access:** Which Ray-Ban device is available? Which supported interface exposes the needed frames? What phone/app, permissions, frame rate, and latency are possible?
- **Compute:** Does inference run on the phone, a nearby computer, or a remote GPU service? What happens when connectivity is lost?
- **Perception:** Which baseline model and task fit the demo: object detection, depth estimation, segmentation, or a combination? What are the relevant hazards and failure cases?
- **Training:** Is fine-tuning needed after a baseline is measured? What licensed/consented data and labels exist? Which metric would show a useful improvement?
- **Belt hardware:** Which controller, motor count and placement, motor drivers, power supply, and communications link are actually available? ESP32 is an option to evaluate, not a confirmed component.
- **Haptic meaning:** Does a pattern describe obstacle direction, urgency, or a suggested action? Define it consistently, with user feedback, before a navigation demo.
- **Demo scope:** What controlled scenario will be shown, with what latency and detection targets? Who supervises and stops the test?

## Suggested first milestones

These are proposed next steps, not completed work or a fixed implementation plan.

1. **Validate input access.** Get timestamped frames through a supported path on the available hardware. If that is blocked, use a webcam or recorded clip as an explicitly labeled development substitute.
2. **Establish a baseline.** Run an existing model on representative, consented or licensed inputs. Record the conditions, errors, and processing time before deciding to fine-tune.
3. **Test belt output independently.** Verify motor addressing and bounded patterns with a manual test input. Establish disconnect and timeout behavior.
4. **Connect a minimal loop.** Feed a clearly defined perception result into an alert policy and then a simulated or real belt. Keep those two modes visibly distinct.
5. **Measure the full path.** Track capture-to-alert latency, missed hazards, false alerts, stale input, and disconnections in a controlled setup.
6. **Decide whether training helps.** If there is a specific measured gap and suitable data, compare fine-tuning against the baseline on held-out examples. Record compute cost and configuration.

## Contracts to define before parallel implementation

These are interface questions, not an existing schema:
- Frame source: image format, capture timestamp, orientation, and sequence identifier.
- Perception result: observation timestamp, detections, confidence, and any depth/risk estimate with its limitations.
- Haptic command: motor/region, bounded intensity and duration, validity period, and interpretation.
- Health state: camera, inference, transport, and belt availability; explicit stale or invalid data behavior.

Capture the selected contract and units in the repository so teammates can build components independently without guessing.

## Safety and privacy

This is an experimental aid. Do not claim it prevents collisions, detects every hazard, or makes a route safe. A lack of alerts is not evidence of a clear path.

Use controlled, supervised demos with a clear stop procedure. Validate motor behavior and failure handling before wearable tests. Keep raw camera data private by default, avoid unnecessary recording, and do not commit identifiable footage, credentials, or private datasets.

## Keeping these notes current

For each important decision, record the date, choice, reason, and evidence or relevant file. For each milestone, distinguish implemented, tested in simulation, tested on hardware, and still unknown. Replace superseded proposals rather than letting conflicting instructions accumulate.
