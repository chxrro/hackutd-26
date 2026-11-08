# Project context: HackUTD 2026

Planning context recorded on 2026-11-07, updated 2026-11-08 after the integration round (PRs #4–#9, #11, #13–#17). These notes capture the project owner's intended direction, not a claim that the hardware or software works.

**Evidence labels used below.** *Unit tests*: automated tests with fakes, no models or hardware. *Simulation*: the real hub code with real models, fed by a file or a scripted phone, driving the simulated belt (`--belt sim`). *Synthetic*: input that is not real camera footage (e.g. a zoomed still photo). *Hardware*: a real phone, glasses or belt. **Nothing merged on 2026-11-07/08 was tested on hardware.** The only hardware results so far are the earlier Camera Bridge bench tests below.

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

## Decisions (2026-11-07 – 2026-11-08)

Owner decisions only. Proposals that nobody has decided are listed separately below.

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
| Person vs. object | Label each tracked box; local = YOLO's COCO `person` class, cloud = Nemotron 3 Nano Omni (vision) via any OpenAI-compatible endpoint, self-hosted on Brev for the demo (`--classifier local\|nim`) | NVIDIA/Nemotron track. A per-frame cloud round trip can't be the collision path, so the cloud only labels boxes the local tracker already follows, at ≤2 Hz. No reply for 3 s → every box falls back to its local label and the debug window shows the error; the belt keeps its collision alerts. Changed 2026-11-07 from "→ fault pattern" after a hub audit: labels don't drive the belt, so a cloud outage (429, network) must not replace real depth/TTC alerts with the fault pattern. If labels ever drive the belt, an unavailable classifier has to become a fault again |
| Stale results (2026-11-07, #5) | Fault when the frame behind the current perception result was received more than 1.0 s ago (`SafetyConfig.result_max_age_s`) | A hub audit found a slow model could keep the belt playing data up to 2.8 s old while reporting healthy. Owner approved 1.0 s; the value is unvalidated |
| Open-vocabulary detector (#11) | Available as opt-in `--detector openvocab` (YOLOE + text prompts); the default stays YOLO11n | Finds some hazards COCO lacks. Kept off by default while the MobileCLIP2 licence (non-commercial research only) is unresolved |
| Optical-flow motion (#9) | Opt-in `--motion flow`, ported from Nathan's `scripts/visual_motion.py` (PR #3) with his agreement; the owner approved it, including mapping a crossing to risk 0.5 in the swept sectors | Camera-relative approach and crossing cues that don't depend on box-height jitter. Flow can only raise risk. All thresholds unvalidated |
| Phone/dashboard control (#14) | `docs/protocol.md` Control section: pause/resume, belt test, alert range presets, classifier switch, record; per-connection pairing with a code printed at hub start; localhost-only dashboard (`--dashboard`) | Lets a supervisor operate the hub from the phone or laptop. Shared contract: the iOS app builds on it |
| Navigation guidance (#13) | Implementation on hold until obstacle alerts work end to end in simulation | Owner decision recorded in [navigation-research.md](navigation-research.md); its patterns and messages are proposals |
| Wearer app (#17) | `ios/` (Ethan's Camera Bridge) overhauled into the wearer-facing app: hub streaming, Control pairing, VoiceOver-first wearer screen, debug-only mock glasses | The owner chose it, per #17's description. This supersedes "the phone app is parked" in the Focus row. Whether `meta/` is retired is still open |
| Integration (2026-11-08) | One integration thread merges into `main`; each PR is tested against current `main` (hub suite + simulation smoke run) and must pass CI (#16) | Four people and their agents work in parallel |
| Pause is felt (2026-11-08) | `pause` plays an outside-in cue on the belt, then a low outer-motor pulse every 15 s (`--pause-reminder`, 0 = off); `resume` plays an inside-out cue. A camera or perception fault plays the fault pattern even while paused. Built from ordinary `levels` commands, no firmware change. See [protocol.md "Belt cues"](protocol.md) | Integrator review of PR #14: pause sent `off`, so the wearer felt the same silence as "no obstacle", and a fault while paused was also silent. AGENTS.md: silence must never stand in for a clear path. Verified only in unit tests and `--belt sim`, not felt on a real belt |

## Proposals, not decided

- **Navigation guidance**, [navigation-research.md](navigation-research.md): pattern set (fault > obstacle > guidance, guidance as a single short tick), a phone → hub `location` message and a `guidance` status field. `docs/protocol.md` is unchanged; these need team agreement before anyone codes against them.
- **A slower open-vocabulary "hazard pass"** (3–5 Hz next to YOLO11n every frame), if YOLOE is too slow live. Not built.
- **A distinct belt cue while alerts are paused.** Today pause sends `off`, so the wearer feels silence, the same as "no alert". Being built on `chxrro/pause-cue`.
- **`record` from the phone/dashboard.** The control action still refuses `true`; `--record` at hub start works.
- **Alert range presets** (`near` 1.5 m / 2.0 s, `normal` 2.5 m / 3.0 s, `far` 3.5 m / 4.0 s) are implemented, but their values are unvalidated guesses.

## Status (2026-11-08)

| Component | Implemented | Verified | Not yet verified |
|---|---|---|---|
| `esp32/` | BLE server, PWM motors, TTL/fault failsafe, serial console | *Unit tests*: 13 native logic tests pass; builds for `esp32dev`. Both run in CI on every PR (#16) | on a real board, motors, wiring, BLE range |
| `hub/` | glasses WS server; webcam/file/stream/replay sources; depth + YOLO perception; opt-in open-vocab detector and optical-flow motion; person/object labels (local or cloud, with local fallback); haptic policy; safety monitor incl. stale-result fault; BLE belt with half-open-connection cleanup; simulated belt; opt-in recording; per-stage latency log; control layer and localhost dashboard | *Unit tests*: 163 pass on `main` `d849972` with the core install and with all extras; CI green. *Simulation*: real models on a synthetic clip through the real CLI, see below | real BLE belt, glasses frames, accuracy of distance/TTC/labels, end-to-end latency with the phone and glasses |
| `meta/` | SDK registration/stream, frame forwarding, hub status UI; device-ready Info.plist, glasses app update button, test-day checklist (#8) | *Unit tests*: 5 protocol tests on the simulator. Signed device build (before #8's rebase) | on an iPhone with the glasses |
| `ios/` | Camera Bridge as the wearer app (#17): HB frames + Control to the hub, wearer and setup screens, VoiceOver announcements, consent before recording, debug-only Meta MockDeviceKit glasses | *Unit tests*: 42 pass (iPhone 16 Plus simulator, integration run). *Simulation* (PR author): mock glasses → app in the simulator → real hub with `--perception sweep --belt sim`; pairing, pause/resume and belt test acked; fault and recovery were announced. Unsigned Release device build | a physical iPhone, real glasses, Wi-Fi between devices, VoiceOver speech heard by a person, a real belt. Not covered by CI |

Person/object classification (2026-11-07, `hub/`): implemented with 26 tests (no models or network; includes cloud timeout/429/outage; at the time an outage faulted the belt, now it falls back to local labels). The real detector + local labels ran on two still images (replayed, not live): ~15–18 ms/frame detector-only, ~42–44 ms with depth, on Apple MPS; the labeling step adds no measurable time. A live webcam test was blocked by macOS camera permission for the agent process. Cloud backend measured on 2026-11-07 against vLLM 0.20 serving Nemotron 3 Nano Omni FP8 on 1× Brev L40S, reached through `brev port-forward` from the laptop. The request was the hub's real one: a 512 px JPEG with numbered boxes, asking for JSON. Round trips, from `haptic-hub nim-bench` on two still photos (20 sequential requests each): median 335 ms (p90 404 ms, 5 boxes, 79 KiB) and 297 ms (p90 343 ms, 3 boxes, 21 KiB); the first request took 1.9 s. On a replayed 15 s clip with the background labeler: 26 replies, median 321 ms, max 882 ms, no failures, and per-frame processing stayed at ~22 ms. Forcing a 50 ms timeout against the real server faulted after the 3 s grace, as designed at the time (now: local-label fallback after 3 s). In those two photos the cloud called a person cut off at the frame edge "object", and a tie box "person". That is an example, not an accuracy figure. Label accuracy is not measured. No live webcam or glasses run with the cloud yet. Too slow for per-frame collision alerts, so the cloud only labels boxes in the background (hybrid).

None of the perception thresholds have been validated against measured distances.

## Milestones merged 2026-11-07/08: evidence and limits

Merged between 23:33 and 00:02 US Central (05:33–06:02 UTC on 2026-11-08). Test counts are the PR's own at merge time unless noted. **No item below was tested on hardware.**

- **#4 Person/object labels** (`hub/`). See the paragraph above. *Unit tests* plus replayed stills; cloud latency was measured against a real Brev endpoint.
- **#5 Stale-result fault** (`hub/`). *Unit tests*: 30 pass, including an end-to-end test with a slow fake model that fails on the old code. Limit: the 1.0 s threshold is a starting value.
- **#6 BLE half-open cleanup** (`hub/`). A connection whose setup failed is now closed, and the reconnect loop survives any error. *Unit tests* with a fake bleak client: 5 new. Limit: the claim that an orphaned connection stops the belt advertising comes from reading the firmware, not from a real belt.
- **#7 Cloud classifier fallback** (`hub/`). Decision above. *Unit tests*: the end-to-end test asserts the belt keeps LEVELS after the cloud drops.
- **#8 `meta/` device readiness**: `external-accessory` keys, "Update glasses app in Meta AI" button, test-day checklist in `meta/README.md`. *Unit tests*: 5/5 protocol tests on the simulator after rebasing. Signed device build before the rebase. Not run on a phone or with glasses.
- **#9 Optical-flow motion** (`--motion flow`, `hub/`). *Unit tests*: 16 new, on synthetic texture. Two parameters changed from Nathan's version because tests forced it: reference gap 0.3 → 0.15 s, box margin 10 % → 0. Cost ~3 ms/frame with 1 box and ~6 ms with 5 (synthetic, CPU). *Simulation*: ran through the real CLI with real models on the synthetic clip and drove the simulated belt. Accuracy on real footage is unknown.
- **#11 Open-vocabulary detector** (`--detector openvocab`, `hub/`). *Unit tests*: 15 new; the detector also works without the vision extra. Timings and hits/misses on 8 Wikimedia hazard photos (*replayed stills*) are in `hub/README.md`. It found stairs, a sign, a raised curb and one bollard; it missed glass, a pole and a fallen bin. One photo per hazard: an example, not an accuracy figure. Adds the `clip` dependency (pinned by commit).
- **#13 Navigation research** (`docs/`). Proposals and citations only; no code.
- **#14 Control layer + dashboard** (`hub/`, `docs/protocol.md`). *Unit tests*: 32 control and 19 dashboard tests. *Simulation* (integration run on the merged tree): a scripted phone paired with the printed code; `get` and `pause` were acked; status went `paused` / belt `off`; the dashboard served on 127.0.0.1; the code never appeared in the logs. Known safety gap: pause sends `off` (silence, see Proposals).
- **#15 Recording, replay, latency** (`hub/`). *Unit tests*: 12 new. *Simulation* on synthetic zoom clips (PR author, Apple MPS): inference p50 34 / p90 36–40 ms; hub total (receive → belt command) p50 61 / p90 82–89 ms; another run was much slower (inference p90 157–177 ms) for an unknown reason. Recordings can contain faces: consent, never commit them.
- **#17 Wearer app in `ios/`.** See the `ios/` row in the status table. New dependency: `MWDATMockDevice` from the already-pinned Meta SDK 1.0.1, Debug builds only. Simulator builds must be signed (`CODE_SIGN_IDENTITY=-`) to run. Details in `docs/camera-bridge.md`.
- **#16 CI** (GitHub Actions, Ubuntu): hub pytest (core install, no models) plus firmware native tests and an `esp32dev` compile. First run on `main` `d849972` passed. iOS is not covered.
- **Integration fixes made while merging.** With `--record`, the recording wrapper didn't pass the hub's control hook to the glasses server, so phone pairing and controls were silently ignored. Fixed in `hub/haptic_hub/record.py` and covered by `tests/test_integration.py`. The other conflicts (#9 vs #11, #14 vs #15) were additive, and both sides were kept.

**Simulation smoke run on `main`** (repeated after each merge). Input: the synthetic clip described below, 504×896 at 15 fps. Real models: Depth Anything V2 Metric Indoor Small + YOLO11n/ByteTrack on Apple MPS. `--belt sim`.
1. **Video source:** health goes `ok`, the belt shows OFF/LEVELS, and Ctrl-C exits cleanly.
2. **Glasses WebSocket path, scripted phone:** HB frame packets at 15 fps over loopback. Status was healthy in 16–17 of 20 samples (the rest were model warm-up), and inference ran at 35–40 ms median. After the phone stopped sending with the socket open, status was `fault` / `no camera frames for 1.7 s`.
3. **`--motion flow`, `--record` + replay, and `--dashboard` with pairing** also ran clean.

This is loopback, not Wi-Fi, and not the iPhone app or glasses.

### Synthetic TTC baseline (2026-11-08)

*Synthetic, one clip; not an accuracy figure.*

**Setup.**
- **Clip:** made from ultralytics' bundled `bus.jpg` by zooming a crop as if walking straight at a person at 1.2 m/s from 4.0 m to 1.8 m. That is 29 frames with a true TTC of 3.33 → 1.53 s, followed by 1.5 s of hold, a 3 s pan and a 2 s wide view.
- **Zoom caveats:** a zoom scales everything equally, so every box has the same true TTC. A still photo has no parallax, and the depth model sees a magnified 2D image.
- **Pipeline:** the real `VisionPerception` (depth + YOLO11n/ByteTrack) and `HapticPolicy`, with frames timestamped in clip time, on Apple MPS. Inference median 34 ms, p90 36 ms, max 48 ms.
- **Sampling:** every 3rd frame was inspected.

**Results.**
- **TTC runs late.** In the middle sector, the estimate was 0.38–0.63 s above the true value at the 7 sampled frames 9–27 (median +0.54 s). The middle motors first vibrated by frame 21 (true TTC 1.93 s), not at sampled frame 18. An exact estimate would have crossed the deadband around frame 9 (true TTC ≈ 2.76 s).
- **Hang-over.** After the approach stopped (frame 28), the middle motors were still on at frame 33 (+0.33 s) and off by frame 36 (+0.53 s).
- **Early false alert.** At frames 3–6, only the far-left motor fired (levels 204, then 121). The cause was a 1.55 s TTC estimate from a box in the left sector, while the true TTC of everything in the zoom was 3.1 s. The bus box spanned all sectors at about 2.9 s, which is below the deadband. The left box was probably a person being cut off at the frame edge as the crop narrowed; not confirmed.
- **Correction.** An earlier integration report described this as "0.3–0.5 s late", "~0.6 s hang-over" and "a false alert on all sectors from the bus box". The numbers here come from the run output.
- **Depth-driven alerts.** The far-left motor stayed at about level 100 through the hold and pan, driven by depth (≈ 2.2 m estimated on the magnified photo), not by TTC.

## Open decisions

Resolve the questions needed for the next experiment rather than choosing an entire stack in advance.

**Waiting on the owner (2026-11-08):**
- **MobileCLIP2 licence.** YOLOE's text encoder is under Apple's ML Research Model terms (non-commercial research only). Accept it for this prototype, or switch `openvocab_model` to YOLO-World (OpenAI CLIP, MIT)? Until then, `--detector openvocab` stays opt-in.
- **`meta/` vs `ios/`.** Two iPhone apps exist: `meta/` (HapticBelt) and `ios/` (Camera Bridge, now the wearer app after #17). Is `meta/` retired, and when? Its device-readiness changes (#8) and the mock-glasses work were partly ported into `ios/`.
- **Nathan's PR #3 and the `chxrro/pr3-fixes` PR #12 into his branch.** His call; the integration thread does not merge them.
- **Brev GPU instances.** Stop or delete them when the cloud classifier isn't being tested. They bill while running, and storage bills until the instance is deleted.

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
