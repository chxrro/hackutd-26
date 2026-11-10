# Project context: HackUTD 2026

Planning context recorded on 2026-11-07, updated 2026-11-08 after the integration round (PRs #4–#9, #11, #13–#17). These notes capture the project owner's intended direction, not a claim that the hardware or software works.

**Evidence labels used below.** *Unit tests*: automated tests with fakes, no models or hardware. *Simulation*: the real hub code with real models, fed by a file or a scripted phone, driving the simulated belt (`--belt sim`). *Synthetic*: input that is not real camera footage (e.g. a zoomed still photo). *Hardware*: a real phone, glasses or belt. The integration-round results were not hardware tests. Hardware evidence includes the Camera Bridge bench tests and the Haptic Belt glasses-to-phone-to-hub bench run below. Real baseline execution on glasses frames and a user-felt phone test vibration are recorded below; detection accuracy, end-to-end alert latency, and belt hardware operation remain unverified.

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

**Haptic Belt bench run (2026-11-08):** Xcode 27.2 built and signed `meta/` with Meta DAT 1.0.1 for a USB-connected physical iPhone 17 running iOS 27.2. `devicectl` confirmed installation of `com.hackutd26.hapticbelt.chirag`; iPhone Developer Mode and pairing were established. Signing uses gitignored `meta/Config/Local.xcconfig`; Wi-Fi entitlements remain enabled. The owner reported live glasses preview, subsequently observed by the agent in Device Hub's physical-phone mirror. No camera footage was recorded.

On CometNet the hub received no physical-phone connection. After the phone and Mac joined a third iPhone's hotspot, the phone connected from `172.20.10.4` to the Mac at `172.20.10.5:8765`, announced streaming, and delivered decodable frames. This establishes the tested hotspot path; it does not prove CometNet's failure was client isolation. The hub used `--source glasses --perception sweep --belt sim` throughout: real phone/glasses input, **fake perception and simulated belt**. The legacy app does not send `frame_source`, so provenance here is established by the visible physical-phone preview and connection, not by that protocol field.

Forwarding diagnosis: initial five-second hub samples were around 6–9 fps. Added phone diagnostics showed all 388 observed drops came from the strict 66 ms forwarding rate gate, with zero in-flight drops in that observation; sampled JPEG/packet creation and send completion each took only a few milliseconds. A two-credit rate limiter now tolerates callback jitter while bounding sustained output to 15 fps; only one frame may be encoded/sent at a time and images are never queued. After installing the fix, four five-second hub windows at 02:54:14–02:54:29 measured 12.0, 12.6, 12.0, and 12.2 incoming fps. These were live bench observations with uncontrolled camera motion, not a controlled performance benchmark. The hub reported stale frames while the app was replaced and recovered to healthy when streaming resumed. The user still reported movement-related phone-preview lag before this fix; glass-capture-to-display latency and glasses transport mode have not been measured.

Validation: the signed device build passed; all 8 iOS hostless tests passed on an iPhone 16 Pro simulator (5 protocol tests and 3 forwarding-rate tests for jitter, sustained rate, and bounded burst after idle). The selected current hub end-to-end, frame-source, and haptic-safety tests passed (36 tests, fakes/simulation). Real perception on glasses frames, metric accuracy, and belt hardware remain untested in this session.


**Phone vibration substitute (2026-11-08):** The owner requested testing the real software baseline while the belt is being built, with the iPhone held as the vibration output. Implemented in `meta/` using existing hub status; no shared protocol or firmware change. The phone summarizes the maximum of five motor levels as a finite 120 ms pulse, no more often than every 0.6 s. It lists obstacle directions on screen rather than claiming spatial haptics. Unreliable input produces three taps no more often than every 3 s. Missing status for 1 s, stale camera timestamps, disconnection, stopped camera, unhealthy status, and malformed levels prevent obstacle pulses. Toggle-off, deliberate camera stop, and app backgrounding stop haptics; fresh status is required after returning. Phone haptics default off at app launch. No continuous looping or scheduled queue of alerts.

Validation: all 17 iOS hostless tests passed on the simulator, including 9 new phone-haptic policy tests; the signed physical-device build and installation passed. A transient SwiftUI style type error in the first build was corrected before these passes. The owner confirmed feeling the sample buzz on the physical iPhone 17. The hub was switched to `--source glasses --perception vision --belt sim --device mps --classifier local --show`: Depth Anything V2 Metric Indoor Small and YOLO11n/ByteTrack running locally. The camera initially reported “No eligible device available,” then recovered after the glasses became available. Real frames produced changing OFF/LEVELS results; at 03:04:52 a five-second hub window showed 12.6 input fps, 12.4 perception fps, zero hub-buffer drops, and 33 ms median / 36 ms p90 inference. These are one live bench sample, not accuracy or end-to-end latency measurements. The physical phone mirror showed vibration enabled and a live obstacle-direction label without a Core Haptics error. The owner subsequently confirmed that real obstacle alerts trigger repeated phone buzzes. Controlled obstacle/clear-scene discrimination and real-phone fault/background behavior still require separate validation. No physical belt connected. Camera footage was not recorded in this initial test; the owner then explicitly requested the recording below.


**Owner-requested server capture (2026-11-08, 03:09–03:11 Central):** Recorded the server renderer with detection boxes/class labels, five sectors, estimated distance/TTC, simulated motor levels, health and latency overlays. Saved locally under gitignored `hub/recordings/baseline-20261108-030930/`: a 59.92 s H.264 MP4 (480×924, about 13 MB), 705 annotated frames and aligned per-render prediction records, plus 779 raw input JPEGs over 65.57 s with capture/receive timestamps. Zero missing referenced files; the raw recorder reported zero dropped frames. The MP4 decoded successfully through FFmpeg. Raw session metadata `duration_s` includes time the hub kept running after the recording limit; use frame timestamps for the actual capture span. Prediction metadata is not ground-truth labeling, and the proposed stationary scene sequence was not independently annotated. Capture adds rendering/disk overhead. The legacy phone's `frame_source` remains `unknown` in raw metadata; physical-phone/glasses provenance was visually checked in this session. The owner confirmed feeling real obstacle-triggered phone pulses before recording. No upload or commit of footage. After capture, the normal real-model hub was restarted without recording.

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
| Person vs. object | Label each tracked box; local = YOLO's COCO `person` class, cloud = Nemotron 3 Nano Omni (vision) via any OpenAI-compatible endpoint, self-hosted on Brev for the demo (`--classifier auto\|local\|nim`). Default `auto` since 2026-11-08: Nemotron when its endpoint answers at startup, else local labels with a warning | NVIDIA/Nemotron track. A per-frame cloud round trip can't be the collision path, so the cloud only labels boxes the local tracker already follows, at ≤2 Hz. No reply for 3 s → every box falls back to its local label and the debug window shows the error; the belt keeps its collision alerts. Changed 2026-11-07 from "→ fault pattern" after a hub audit: labels don't drive the belt, so a cloud outage (429, network) must not replace real depth/TTC alerts with the fault pattern. If labels ever drive the belt, an unavailable classifier has to become a fault again |
| Stale results (2026-11-07, #5) | Fault when the frame behind the current perception result was received more than 1.0 s ago (`SafetyConfig.result_max_age_s`) | A hub audit found a slow model could keep the belt playing data up to 2.8 s old while reporting healthy. Owner approved 1.0 s; the value is unvalidated |
| Open-vocabulary detector (#11) | Available as opt-in `--detector openvocab` (YOLOE + text prompts); the default stays YOLO11n | Finds some hazards COCO lacks. Kept off by default while the MobileCLIP2 licence (non-commercial research only) is unresolved |
| Optical-flow motion (#9) | Opt-in `--motion flow`, ported from Nathan's `scripts/visual_motion.py` (PR #3) with his agreement; the owner approved it, including mapping a crossing to risk 0.5 in the swept sectors | Camera-relative approach and crossing cues that don't depend on box-height jitter. Flow can only raise risk. All thresholds unvalidated |
| Phone/dashboard control (#14) | `docs/protocol.md` Control section: pause/resume, belt test, alert range presets, classifier switch, record; per-connection pairing with a code printed at hub start; localhost-only dashboard (`--dashboard`) | Lets a supervisor operate the hub from the phone or laptop. Shared contract: the iOS app builds on it |
| Navigation guidance (#13) | Implementation on hold until obstacle alerts work end to end in simulation | Owner decision recorded in [navigation-research.md](navigation-research.md); its patterns and messages are proposals |
| Wearer app (#17) | `ios/` (Ethan's Camera Bridge) overhauled into the wearer-facing app: hub streaming, Control pairing, VoiceOver-first wearer screen, debug-only mock glasses | The owner chose it, per #17's description. This supersedes "the phone app is parked" in the Focus row. Whether `meta/` is retired is still open |
| Integration (2026-11-08) | One integration thread merges into `main`; each PR is tested against current `main` (hub suite + simulation smoke run) and must pass CI (#16) | Four people and their agents work in parallel |
| Belt status characteristic (2026-11-08) | The firmware reports its mode, last applied command seq, motor levels, uptime, build id and `kMaxDuty` over a BLE read + notify characteristic; the info characteristic carries the build id; the hub turns "belt says expired / fault / not applying / silent" into `healthy: false`. See [protocol.md "Status"](protocol.md). Additive: old firmware and old hubs still work | The owner approved the firmware change. A write acknowledgement only shows the BLE stack got the bytes (PR #23); PR #21 listed the missing status, build id and `kMaxDuty`. *Native tests* and *simulation* only (`belt_not_applying` scenario); not run on a real board or radio |
| Pause is felt (2026-11-08) | `pause` plays an outside-in cue on the belt, then a low outer-motor pulse every 15 s (`--pause-reminder`, 0 = off); `resume` plays an inside-out cue. A camera or perception fault plays the fault pattern even while paused. Built from ordinary `levels` commands, no firmware change. See [protocol.md "Belt cues"](protocol.md) | Integrator review of PR #14: pause sent `off`, so the wearer felt the same silence as "no obstacle", and a fault while paused was also silent. AGENTS.md: silence must never stand in for a clear path. Verified only in unit tests and `--belt sim`, not felt on a real belt |
| Deliberate stop (2026-11-08) | Protocol addition: paired-only control action `end_session`, sent by the wearer's Stop before the camera stops. The hub plays a session-ended cue (three fading pulses on the inner motors), then stays quiet with `healthy: false`, `reason: "stopped by wearer"`, `stopped: true`. A frame or `phone_status` `"starting"` starts a new session (resume cue). A phone lost without it (crash, Wi-Fi, backgrounded) or an unpaired sender still gets the fault pattern | Before, Stop paused the hub, then "no camera frames" outranked the pause, so the belt played the fault pattern for as long as the hub ran after a deliberate stop. Verified in unit tests, the sim (`user_stop`, `phone_vanishes`, `user_stop_unpaired`) and one simulator run (mock glasses, `--belt sim`); not felt on a real belt |

## Attributed hazards and glasses speech build (2026-11-08)

Owner-approved next build on `codex/hazard-speech`, building on the uncommitted
phone haptic/forwarding work above. Added optional versioned `hazards` status
in `hub/` and documented it in `docs/protocol.md`; `meta/` consumes it, older
clients can ignore it, and ESP32 commands/pins are unchanged. A hazard references
the perception result used for the motor command, its age, sector/level, winning
evidence, and attributed label/track/motion. Depth-only risk stays unlabeled;
labels no longer come from the largest unrelated box. Smoothed release tails
below the raw-risk deadband have unknown attribution. Existing optical-flow
approach/crossing results retain their contributing track. Risk thresholds and
motor strength mappings were not changed.

The iPhone has **Test glasses speech**, **Speak obstacle alerts**, and brief versus
object/motion wording. One pure assessment validates freshness and hazard/level
agreement for both phone haptics and speech. Speech is generated locally using
AVSpeechSynthesizer and requires a selected Bluetooth output; it does not call
Meta AI or request microphone permission. Obstacle speech has a three-second
minimum change interval, unchanged hazards are not repeated, obsolete utterances
are canceled, faults preempt obstacle speech, and automatic speech pauses for
VoiceOver/backgrounding. A selected route is not proof of audible delivery.
Camera-relative direction is explicitly distinguished from torso heading.

Validation: **350 hub tests passed**, including new attribution/command snapshot
checks; **25 iOS hostless tests passed**, including malformed/stale hazard data,
legacy fallback, common haptic levels, speech deduplication and cancellation.
The first full hub run had one old test expecting unrelated sector labels; it
was updated to assert the corrected attribution while preserving classified
object labels, and the full rerun passed. Signed iPhone build and physical
installation passed; Device Hub showed the new controls and a successful hub
connection. A real-model replay of 120 previously recorded glasses frames with
`motion=flow` produced JSON-serializable hazard snapshots matching motor levels
(summary in ignored `hub/recordings/baseline-20261108-030930/hazard-build-smoke.json`).
This is replay integration evidence, not labeled motion/detection accuracy.

Physical audio check is blocked: the phone reported glasses not detected, and
iOS Control Center listed only iPhone Speaker. The app correctly refused the
Bluetooth speech test without an audio route. Audible delivery, simultaneous
live camera/audio performance, interruptions and route changes on hardware
remain unverified. The local hub was restarted with real vision, local
classification, `--motion flow --belt sim --show`, without recording; at this
handoff it is waiting for camera frames. Both phone output toggles remain off.
Hand suppression and validated motion classification are still future work.

**Audio-route follow-up:** The owner reported a persistent "select glasses"
warning despite a connected accessory. Inspection found that the app retained
a prior route error even after a later route update. Added an in-app system
audio-output picker, volume display, foreground/periodic route refresh, and
current-state warning logic; speech configuration is no longer reset for each
utterance. Bluetooth pairing and Meta camera connectivity do not themselves
establish the selected playback output. The signed physical build and installation passed, and all 25 hostless iOS
logic tests passed. A first picker integration configured audio during picker
presentation; setup was moved before presentation, and the final installed
build visibly opened the native system picker. It listed only iPhone Speaker;
the app reported 0% output volume. Audible speech remains blocked by the missing
Bluetooth playback route. This does not establish why the accessory is absent
from iOS's route list; the owner was asked whether another app on the same phone
can play audio through the glasses.

**Speech playback fix and hardware confirmation (2026-11-08):** After reconnecting,
the physical iPhone reported **RB Meta 02JV**, volume 50%, but the test initially
did not register a speech request. Inspection found a stale interruption flag
could silently reject speech even after successful session activation. The fix
clears that flag on successful activation, exposes blocked request reasons, and
uses synthesizer callbacks to display playback state. A five-second start timeout
cancels a request that never starts. The signed device build and installation
passed. The phone then visibly reported speaking through RB Meta 02JV and the
owner confirmed **"its working !!!"**. This verifies audible manual speech on
the real glasses; it does not verify concurrent live camera/audio, automatic
hazard speech, latency, or all interruption/disconnect cases.
All 26 hostless iOS logic tests passed, including a regression check that the
disabled automatic-speech watchdog does not cancel a manual test. These tests
do not exercise AVAudioSession or real audio hardware. `git diff --check` passed.

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

Person/object classification (2026-11-07, `hub/`): implemented with 26 tests (no models or network; includes cloud timeout/429/outage; at the time an outage faulted the belt, now it falls back to local labels). The real detector + local labels ran on two still images (replayed, not live): ~15–18 ms/frame detector-only, ~42–44 ms with depth, on Apple MPS; the labeling step adds no measurable time. A live webcam test was blocked by macOS camera permission for the agent process. Cloud backend measured on 2026-11-07 against vLLM 0.20 serving Nemotron 3 Nano Omni FP8 on 1× Brev L40S, reached through `brev port-forward` from the laptop. The request was the hub's real one: a 512 px JPEG with numbered boxes, asking for JSON. Round trips, from `haptic-hub nim-bench` on two still photos (20 sequential requests each): median 335 ms (p90 404 ms, 5 boxes, 79 KiB) and 297 ms (p90 343 ms, 3 boxes, 21 KiB); the first request took 1.9 s. On a replayed 15 s clip with the background labeler: 26 replies, median 321 ms, max 882 ms, no failures, and per-frame processing stayed at ~22 ms. Forcing a 50 ms timeout against the real server faulted after the 3 s grace, as designed at the time (now: local-label fallback after 3 s). In those two photos the cloud called a person cut off at the frame edge "object", and a tie box "person". That is an example, not an accuracy figure. Label accuracy is not measured. No live webcam or glasses run with the cloud yet. Too slow for per-frame collision alerts, so the cloud only labels boxes in the background (hybrid). Local run measured 2026-11-08 (replaced Brev for the demo): llama.cpp build 12110 `llama-server` serving Unsloth's Nemotron 3 Nano Omni GGUF (`MXFP4_MOE` + `mmproj-F16`) on an M4 Max laptop with 36 GB, via `hub/scripts/serve-nemotron-local.sh`. `nim-bench`, 20 sequential requests per still photo (Ultralytics sample images, not our footage): median 594 ms (p90 637 ms, 5 boxes) and 279 ms (p90 298 ms, 3 boxes); first request 1.0–1.9 s; no failures. The hub with default settings picked the local server at startup and ran 70 s on a replayed recording with the simulated belt: ~43 label replies, no classifier errors, belt kept getting LEVELS. Not yet run with the webcam or the glasses, and per-frame hub timing with the model sharing the GPU was not measured.

Simulated world (2026-11-07, `hub/haptic_hub/sim/`, **simulation only**): a ray-cast world renders glasses frames (504×896, 15 fps; the 60° horizontal FOV is assumed, not measured). A fake phone sends them as `HB` packets over the real WebSocket server to the real hub. The hub's belt commands go into the real `esp32/lib/belt_core` logic compiled for the host. With ground-truth stand-ins for the models, all 24 gating scenarios pass: direction, multiple obstacles, approaching person vs. still pole, head motion, low-confidence detections, stale frames, phone disconnect, model crash/stall, and lost belt writes leading to the firmware fault pattern. Each check was shown to fail on a deliberately broken hub. Probes found these limitations of the current thresholds/geometry: a ceiling or a 15–20° downward head pitch makes all motors buzz; low (0.45 m) obstacles are missed; detections below `detector_conf` get no early warning or uncertainty signal; box-growth TTC reads ~0.35 s late; box jitter can intermittently trigger TTC alerts at 5–7 m. These are not real-world results. See `hub/haptic_hub/sim/README.md`.

Hardware bring-up kit (2026-11-08, `hub/` + docs): `haptic-hub bench` (motor map, levels, TTL failsafe, fault by command and by dropped link, reconnect, BLE write timing, JSON report; optional `--serial` to time TTL expiry from the belt's log) and the runbook [hardware-bringup.md](hardware-bringup.md). Tested only in simulation: the real `belt_core` logic compiled for the host (TTL 250 ms observed at ~254 ms), a Python TTL model, a fake BLE client and a pty serial port. **Not yet run on the real belt.** The runbook lists firmware/protocol gaps found (no BLE status characteristic, no firmware version in info) as proposals.

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

## Perception stability experiment (2026-11-08)

The owner requested better model behavior and additional functionality after
confirming audible glasses speech. Work is on `codex/perception-stability`,
carrying the owner's existing, uncommitted speech/phone prerequisites; no commit
or push was requested. This iteration touches the hub and documentation and
introduces no dependencies or wire-contract changes.

Added opt-in `--alert-profile stable`: moderate new sector alerts need three
fresh results spanning 200 ms; risk ≥ 0.8 bypasses that confirmation. Faults,
quiet observations, clock/sequence restarts, and gaps over 300 ms reset pending
evidence. Release is based on elapsed time. A robust inverse-box-height TTC fit
rejects jitter, poor fits, abrupt size changes, clipped boxes, and insufficient
history. Depth and optical-flow contributions are preserved. These are proposed,
unvalidated thresholds and add warning delay; model weights are unchanged.
The default profile and the currently running baseline hub are unchanged.

Added `haptic-hub evaluate` to compare profiles offline using identical cached
depth/detector predictions and independent motion/policy histories. It writes
parameters and per-frame decisions, not camera images, to a new local report.
No belt is connected and no frames are uploaded; model registry access may
occur during model loading. Outputs remain under gitignored recordings.

**Real-model replay evidence:** All 779 frames of the owner-requested glasses
recording processed successfully on local MPS with optical flow. Report:
`hub/recordings/baseline-20261108-030930/stability-comparison.json`.
Over 62.81 s of intervals no longer than 300 ms, baseline versus stable produced
140 versus 70 sector alert onsets and 32.57 versus 20.55 s with any obstacle
alert. No policy fault frames occurred. Longer gaps and the final frame's
unknown duration are excluded. These are behavior counts, not accuracy,
false-positive rates, or live latency. A spot check of three frames whose alerts
were removed included visible chairs/tables; there are no ground-truth hazard
labels to establish whether suppression was desirable. Keep the new profile
opt-in pending labeled replay and a controlled stationary live test.

Hand recognition/suppression, true stationary-versus-moving classification,
calibrated distance, and model fine-tuning are not implemented by this change.
Near hands can still trigger the immediate high-risk path. Next data work:
label short intervals for hand-in-view, clear view, nearby obstacles, head
turns, approach and crossing, then measure missed hazards and nuisance alerts
separately before adjusting thresholds or choosing new model weights.

Validation: final full hub suite **368 passed**; `git diff --check` passed.
An initial full run had one test-stub failure because the stub did not accept
TTC's new `clipped` keyword. The stub was updated, its attribution tests passed,
and the full suite then passed. New tests cover moderate flashes, sustained
risk, urgent escalation, independent sectors, heartbeat deduplication,
time/gap resets, time-based release, invalid risks, TTC jitter/clipping,
preserved depth alerts, and replay input/cache behavior. Live stable-profile
camera/phone output and real belt checks have not been run. No iOS source
changed in this iteration, so iOS tests/build were not rerun.

## Phone vibration and voice pattern build (2026-11-08)

The owner selected **more useful vibration and voice patterns** as the next
priority. Implemented in `meta/` on `codex/alert-patterns`, preserving the prior
uncommitted work. No firmware, hub decision, or wire-contract change in this
iteration. The phone remains a substitute while the belt is unfinished.

- Notice: one finite 120 ms buzz, repeating every 1.2 s while current.
- Higher urgency: two 120 ms buzzes starting 200 ms apart, repeating every
  0.6 s. Escalation replaces the current pattern immediately. Enter at hub
  motor level 220, retain until below 200; these are unvalidated strength
  thresholds, not measured distances or collision probabilities.
- Unavailable input: three transient taps, repeating every 3 s. Quiet,
  disabled, and inactive states cancel patterns; none loop or queue.
- Phone strength slider: 20–100%, default 70%; affects future phone cues only.
- Voice: prefix higher urgency with “Urgent,” allowing escalation before the
  normal three-second speech cooldown, with a two-second escalation guard.
  Optional ongoing reminders (default enabled) repeat notices every 12 s,
  urgent alerts every 6 s, and unavailable state every 15 s, using the latest
  assessment. Repeat-current-alert re-evaluates freshness; no-current-alert
  speech explicitly says this is not evidence of a clear path.
- Pattern practice: choose notice, higher urgency, or unavailable. Play a
  bounded phone pattern plus an explanation labeled “Pattern test.” Both
  automatic output toggles turn off during practice and must be re-enabled.
  Voice describes obstacle direction, never a direction to travel.

Validation: **35 hostless iOS tests passed**, including nine new pattern,
urgency, reminder, repeat, and intensity-bound checks. An initial old cadence
expectation failed after the deliberate interval change; updated it to assert
the new 1.2 s cadence with fresh status, then the suite passed. Signed generic
iOS device build passed; `git diff --check` passed. Physical iPhone 17 was
reported unavailable by devicectl, so this build has **not been installed or
felt/heard on hardware**. Simulator launch reached the existing Meta SDK
startup error (`WearablesError(rawValue: 0)`), blocking UI interaction beyond
that screen. The unit tests do not require the Meta SDK. No claim of live
camera/audio coexistence, accessible usability validation, or real belt
pattern verification. Reconnect/unlock the iPhone for installation and a
stationary pattern comparison; verify both output toggles are re-enabled only
after practice finishes. No capture, upload, commit, or push.

## Direct iPhone-to-ESP32 belt connection (2026-11-08)

Owner requested ESP32 belt connection support. Added in `meta/` on
`codex/iphone-belt-ble`, carrying the owner's prior uncommitted build work.
The app can own the BLE connection while the existing hub runs inference with
`--belt sim`. It scans by the existing service UUID, presents devices for user
selection, validates protocol v1/five motors, requires acknowledged command
writes, and subscribes to optional firmware status. The app separately displays
local connection state, firmware build/mode/levels/PWM cap, acknowledgement
counts and whether firmware reports recent commands applied. These are not
proof that motors physically moved.

Relay defaults off and is explicitly enabled after connecting. It forwards
five sector levels from fresh, healthy, validated hub snapshots unchanged;
stale, malformed or disconnected input requests fault. The hub's transport
must report `simulated` to avoid competing BLE owners. A 10 Hz writer permits
one outstanding write with 250 ms TTL and acknowledgement deadline, with no
queue. A 200 ms producer lease independently expires live intent to fault.
Optional firmware reports are matched by recent sequence, mode, levels and
command age; missing or mismatched reports after startup grace drop the link.
Unexpected link failure disarms relay and exposes a local belt fault to the
phone's shared haptic/speech assessment. Reconnect and re-arm are manual.

A bench control selects one of the five motors and requests level 120 within
a 200 ms window, then off, with firmware TTL as fallback; this does not claim
an exact physical pulse duration. Disabling relay sends off heartbeats while
connected. Stopping the camera disables relay; leaving the active app drops
BLE. The existing firmware may then play its link-loss cue for up to 30 s
before dormancy, even if an off packet was delivered first.

The protocol document now describes this alternate owner and route. Wire
formats, UUIDs, pins, firmware and dependencies are unchanged. This affects
hub/firmware teammates operationally: run one BLE owner; the hub's simulated
transport and healthy inference are not confirmation of phone-side wearable
delivery. Phone BLE telemetry is not yet published back to the hub. Existing
hub pause/resume/test/session cue timelines are not relayed by this first
status bridge; unhealthy hub states map to fault. Phone vibration patterns and
its strength slider remain separate from belt motor levels.

Validation: 48 hostless iOS tests passed, including 13 BLE protocol/relay tests
covering shared command/status golden vectors, incompatible metadata, stale
input, per-sector levels, transport ownership, single-write/rate limits,
sequence wrap, acknowledgement deadlines, firmware-status matching and
producer/test expiry. Signed generic iOS build passed. These are logic/build
checks, not a real BLE test. Hardware installation and bench testing remain
deferred per the owner; no scan, motor activation, camera capture, commit or
push was performed for this task. Hub/firmware suites were not rerun because
those components were not changed in this iteration.

**Device installation follow-up (2026-11-08):** After the owner reconnected
both phones, devicectl confirmed installation of the latest signed build
(including alert patterns and phone-to-ESP32 BLE support) on the original
physical iPhone 17, `iPhone (298)`. The first device inventory briefly showed
only the other phone; its installation attempt was blocked by disabled
Developer Mode. No Developer Mode change on that other phone was needed after
the original iPhone appeared. The Mac's active hotspot address is
`172.20.10.5`; a WebSocket connection to `ws://172.20.10.5:8765` received hub
status with `belt: simulated` and a no-camera-frames fault. The hub is running
and waiting for streaming. This verifies installation and local hub reachability,
not a fresh phone-to-hub connection, audible pattern test, or real belt test.

**BLE timeout investigation (2026-11-08):** The owner reported the 250 ms
write-acknowledgement error and repeated double vibrations. Inspection found
the client could treat a previously acknowledged write as an outstanding
request when a heartbeat ran late. On `codex/belt-ack-recovery`, the app now
separates the unchanged 250 ms delivery/firmware TTL from a one-second timeout
for an actually outstanding ATT write. Late delivery disarms live alerts and
tries off commands without immediately disconnecting; recovery requires three
timely acknowledgements and fresh matching firmware status when available.
Re-enabling alerts remains manual. Missing firmware status for three seconds
or a write still outstanding after one second disconnects. The UI displays
acknowledgement latency, which includes app callback scheduling. No firmware,
wire format, pin assignment, or motor TTL changed.

Validation: 51 hostless iOS tests passed, the signed physical-device build
passed, and devicectl installed it on the original iPhone 17. A local hub
status check after restoring streaming reported healthy input. Physical BLE
testing with motor alerts off showed real delayed acknowledgements (one
279 ms sample and a later one-second timeout), followed by a separate
connection that recovered and accumulated acknowledged writes. The connected
belt exposed no firmware status and reported an unknown build; acknowledged
writes do not prove motor behavior. The owner subsequently said the belt was
not currently close to the phone and steadily powered in response to that
combined question. The specific distance/power condition was not established,
so neither it nor camera streaming is a confirmed cause. No motor test was
triggered, and a reliable full hardware run remains unverified.

**Subsequent crash/cycle report (2026-11-08):** The owner reports that the app
now registers some kind of crash and cycles. Both physical iPhones were
unavailable in devicectl, and requesting the original phone's system crash
logs failed with CoreDevice error 4016. Only previous-day local HapticBelt
crash reports were present; these do not establish the current failure.
Requested USB reconnection/unlock and clarification whether the app exits or
stays open with repeated errors. No new crash cause or fix is claimed, and
no further runtime change was made without evidence. Current hardware
verification is blocked on access to the phone. No commit, push, or footage
collection was performed.

**USB firmware and streaming investigation (2026-11-08):** After the owner
reconnected the original iPhone and ESP32, the app remained open; the inspected
phone crash-log listing contained no Haptic Belt reports. The hub had stopped.
Restarting the local MPS vision hub restored real-frame inference and removed
the server retry error. This establishes a server availability problem, not an
app crash diagnosis.

Flashed the current firmware, adding its existing status reporting to the
previously unidentifiable belt. USB logs now prove repeated command TTL expiry
and recovery to off, including with obstacle alerts disabled. The firmware
requests a 15–30 ms BLE interval, zero peripheral latency and 4 s supervision;
the phone negotiated 30 ms / zero latency / 4 s. The 250 ms command TTL, motor
pins, wire formats and PWM cap remain unchanged. Expiries also occurred during
a camera-paused interval, so streaming alone is not an established cause.

Moved iOS BLE callbacks and the heartbeat to a dedicated serial queue, with
throttled UI snapshots. Producer leases retain their original creation time
across the queue boundary. Added a longest-command-gap diagnostic. Validation:
52 hostless iOS tests passed, including a worker heartbeat check while the main
thread is blocked; signed build and installation on the original iPhone 17
passed. The firmware native suite passed 19 tests; the subsequent BLE parameter
change compiled and flashed successfully. These checks do not establish radio
reliability.

The owner confirmed camera, hub and belt connected with alerts off after the
new installation. The following USB observation still showed repeated
`expired` → `off` transitions. During that observation the hub reported zero
incoming frames, so a sustained full-streaming test of this build is still
pending. Requested the phone's command-gap/acknowledgement readings and frame
counter to distinguish scheduling from transport delays. No new motor test or
automatic-alert arming was performed; the BLE delivery issue is **unresolved**.
No recording, commit or push in this investigation.

**Glasses-off isolation (2026-11-08):** The owner confirmed that the glasses'
battery had died and asked to continue debugging without them. Added USB-only
firmware diagnostics for received payload gaps, callback-to-loop queue delay,
dropped events and RSSI, without changing BLE packets or motor behavior. The
device build, upload and all 19 native firmware tests passed. A Mac BLE
comparison was blocked waiting for CoreBluetooth's initial state callback;
the scan was cancelled before it found or connected to a belt.

After the owner reconnected the iPhone with alerts off, the new USB log showed
1,070 received commands over approximately 124 seconds after the first
command, with no expiry or disconnect. Maximum receive gap was 240 ms, maximum
firmware queue delay 10 ms, and dropped event count zero. This rules out a
large firmware queue backlog during this sample, not intermittent app or radio
delay in general. RSSI samples varied widely, so no single reading establishes
an antenna fault. This was a glasses-off, off-command test; it does not validate
streaming coexistence or automatic obstacle alerts. The owner confirmed a
brief center-motor vibration that stopped. Multiple `levels` → `off` transitions
were observed; the owner confirmed tapping the test button several times.
One later TTL expiry coincided with a measured receive gap of 270 ms while
the maximum firmware queue delay remained 10 ms. More than 60 seconds of
untouched off heartbeats then produced no further expiry. Thus the motor
bench test worked, but delivery was not fully reliable even without glasses.

The next iOS change caps sends at 15 Hz instead of 10 Hz (matching the hub's
existing command budget), still allowing only one outstanding write and
keeping the 250 ms TTL and 200 ms producer lease. With prompt acknowledgements
the 25 ms worker timer can now send approximately every 75 ms, increasing
retry margin. This is a mitigation to validate, not an established root-cause
fix. Updated the phone documentation and shared protocol's timing description;
packet formats, UUIDs and firmware watchdog behavior are unchanged. All 53
iOS hostless tests passed, including the new cadence/deadline regression.

**Cadence mitigation hardware check:** Signed device build and installation
passed on the original iPhone 17. After the owner reconnected with alerts off,
USB diagnostics recorded 1,659 commands over approximately 130 seconds after
the first command, with no TTL expiries, disconnects or dropped queue events.
Maximum received-command gap was 180 ms and maximum queue delay was 7 ms.
This verifies the off-heartbeat bench run on the updated build, not a controlled
radio benchmark or a proven root cause. The earlier owner-felt motor pulse
preceded the cadence update. The glasses were still unavailable; streaming,
live obstacle relay, and motor-load coexistence on this final build remain to
be checked after charging. Automatic belt alerts remain off. Firmware tests:
19 passed; iOS tests: 53 passed; device build/upload/install and diff checks
passed. No commit, push or camera recording.
