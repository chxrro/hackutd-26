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

## Standalone Camera Bridge diagnostic

- **2026-11-07.** The owner selected iPhone as the phone paired with the glasses. The first experiment uses a native iPhone app, Meta's iOS Device Access Toolkit in Developer Mode, and a computer viewer on the same local network. The test pattern uses the same JPEG uploader as the Ray-Ban adapter. See [Camera Bridge setup](camera-bridge.md).
- The viewer is designed to keep only the most recent frame, label simulated input, and clear stale frames. The phone drops frames while an upload is active and stops sending when backgrounded.
- **2026-11-07.** The owner approved SDK use for the local prototype. Xcode 27.2 resolved the pinned Meta iOS SDK `1.0.1`. The unsigned arm64 device build passed. The app launched on an iPhone 17 simulator and sent changing JPEGs to the Mac viewer over loopback. After sending stopped, the viewer expired the last frame. The build and loopback test did not use physical glasses.
- Earlier checks passed with synthetic inputs for the HTTP handler, viewer status transitions, and Swift uploader address and pairing validation. Camera Bridge 0.1 installed and launched on an iPhone 16 Pro over USB after the owner enabled Developer Mode and trusted the development signing profile. The physical phone's test pattern could not reach the Mac over the network in use. The viewer was listening on the Mac and responded locally. The glasses test is pending Meta AI installation and pairing. Firmware compatibility, end-to-end latency, and delivered frame rate remain unverified.
- The team switched the bench test to an iPhone 17 with Meta AI installed and the glasses already paired, as reported by the owner. Xcode built Camera Bridge for that phone, and `devicectl` confirmed installation and launch after the phone trusted its signing profile. After the phone updated from iOS 26.6 to 27.2, Device Hub mirrored its screen over USB. Camera Bridge reported that it connected to Meta AI, and Meta AI opened its separate glasses camera permission prompt. The iPhone 17 test pattern could not reach the Mac viewer over the current Wi-Fi even after the local network prompt was dismissed. The Mac viewer responded locally with `204` and `X-Frame-State: waiting`. No frames from the iPhone 17 have been verified.
- With no phone hotspot available, Camera Bridge added an on-screen preview for Device Hub mirroring. The signed build ran on the iPhone 17, the simulated frame counter advanced in the Mac window over USB, and stopping cleared the preview. The physical Ray-Ban Meta Gen 2 session reported `Device unavailable` before the tester put on the glasses. When worn, the SDK reported that the app component on the glasses requires an update. The camera session did not produce frames in either attempt.
- The handoff build adds an **Open glasses app update** shortcut to Meta AI. The unsigned iOS build passed in Xcode 27.2. The shortcut still needs a physical test, followed by a live preview after the glasses app update.

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

## Decisions (2026-11-07)

| Decision | Choice | Why |
|---|---|---|
| Structure | `esp32/` (belt), `hub/` (center), `meta/` (phone) | Owner asked for three distinct, modular sections |
| Phone | iPhone, Meta Wearables DAT in Developer Mode | Owner's phone; no Managed Meta account needed for a team-only prototype |
| Compute | Python hub on a laptop | Easiest to iterate on and train models; the phone only forwards frames |
| Belt link | ESP32 + BLE GATT, 5 motors across the front | Glasses only see forward; ESP32 has BLE built in |
| Contracts | [docs/protocol.md](protocol.md), with golden vectors tested in all three sections | Lets teammates work in parallel |
| Haptic meaning | Motor = obstacle in that direction (not "move this way"); a distinct fault pattern means "not reliable" | AGENTS.md safety rules |
| Perception baseline | Depth Anything V2 Metric Small + YOLO11n/ByteTrack box-growth TTC | Pretrained baseline before any fine-tuning |
| Focus | Owner: prioritize `esp32/` and the hub; the phone app is parked as is | Owner request |
| Person vs. object | Label each tracked box; local = YOLO's COCO `person` class, cloud = Nemotron 3 Nano Omni (vision) via any OpenAI-compatible endpoint, self-hosted on Brev for the demo (`--classifier local\|nim`) | NVIDIA/Nemotron track. A per-frame cloud round trip can't be the collision path, so the cloud only labels boxes the local tracker already follows, at ≤2 Hz; no reply for 3 s → fault pattern |

## Status (2026-11-07)

| Component | Implemented | Verified | Not yet verified |
|---|---|---|---|
| `esp32/` | BLE server, PWM motors, TTL/fault failsafe, serial console | builds for `esp32dev`; 13 native logic tests pass | on a real board, motors, wiring, BLE range |
| `hub/` | glasses WS server, webcam/file/stream sources, depth + YOLO perception, haptic policy, safety monitor, BLE + simulated belt | 27 tests pass (incl. end-to-end with a fake phone and simulated belt); real models ran on a sample video at ~37 ms/frame on Apple MPS | real BLE belt, glasses frames, accuracy of distance/TTC, end-to-end latency |
| `meta/` | SDK registration/stream, frame forwarding, hub status UI | builds for the simulator; 5 protocol tests pass | on an iPhone with the glasses |

Person/object classification (2026-11-07, `hub/`): implemented with 26 tests (no models or network; includes cloud timeout/429/outage → belt fault). The real detector + local labels ran on two still images (replayed, not live): ~15–18 ms/frame detector-only, ~42–44 ms with depth, on Apple MPS; the labeling step adds no measurable time. A live webcam test was blocked by macOS camera permission for the agent process. Cloud backend measured on 2026-11-07 against vLLM 0.20 serving Nemotron 3 Nano Omni FP8 on 1× Brev L40S, reached through `brev port-forward` from the laptop. The request was the hub's real one: a 512 px JPEG with numbered boxes, asking for JSON. Round trips, from `haptic-hub nim-bench` on two still photos (20 sequential requests each): median 335 ms (p90 404 ms, 5 boxes, 79 KiB) and 297 ms (p90 343 ms, 3 boxes, 21 KiB); the first request took 1.9 s. On a replayed 15 s clip with the background labeler: 26 replies, median 321 ms, max 882 ms, no failures, and per-frame processing stayed at ~22 ms. Forcing a 50 ms timeout against the real server faulted after the 3 s grace, as designed. In those two photos the cloud called a person cut off at the frame edge "object", and a tie box "person". That is an example, not an accuracy figure. Label accuracy is not measured. No live webcam or glasses run with the cloud yet. Too slow for per-frame collision alerts, so the cloud only labels boxes in the background (hybrid).

None of the perception thresholds have been validated against measured distances.

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
