# Project context: HackUTD 2026

Planning context recorded on 2026-11-07. These notes capture the project owner's intended direction, not a claim that the hardware or software works.

## Goal and intended users

Build an accessibility prototype for blind users that turns visual collision risks into tactile alerts. The proposed experience uses Ray-Ban camera video and a vibration belt so the wearer can receive obstacle alerts without depending on a screen.

## Confirmed project direction

- Use Ray-Ban video as the intended visual input.
- Detect obstacles or collision risks and communicate alerts through a vibration belt.
- Explore whether fine-tuning an existing vision model would help.
- Use NVIDIA Brev hackathon credits for a Cosmos3-Edge reasoner prototype on a rented GPU.

Fine-tuning remains exploratory. On November 7, 2026, the owner authorized NVIDIA Brev account setup, redemption of hackathon credits, and a Cosmos3-Edge reasoner deployment. This authorizes the prototype GPU setup described in [the Brev notes](brev-reasoner.md).

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

## Status (2026-11-07)

| Component | Implemented | Verified | Not yet verified |
|---|---|---|---|
| `esp32/` | BLE server, PWM motors, TTL/fault failsafe, serial console | builds for `esp32dev`; 13 native logic tests pass | on a real board, motors, wiring, BLE range |
| `hub/` | glasses WS server, webcam/file/stream sources, depth + YOLO perception, haptic policy, safety monitor, BLE + simulated belt | 27 tests pass (incl. end-to-end with a fake phone and simulated belt); real models ran on a sample video at ~37 ms/frame on Apple MPS | real BLE belt, glasses frames, accuracy of distance/TTC, end-to-end latency |
| `meta/` | SDK registration/stream, frame forwarding, hub status UI | builds for the simulator; 5 protocol tests pass | on an iPhone with the glasses |

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

## Brev reasoner setup — November 7, 2026

- Owner confirmed standard Ray-Ban glasses paired with an iPhone; the exact glasses model and real camera access remain to be verified.
- Brev CLI installation, NVIDIA/Brev authentication, and credit-code redemption succeeded. The billing dashboard showed $60.00 credit. No payment card was added or auto-recharge enabled.
- A single AWS A10G instance named `hackutd-reasoner` was created for Cosmos3-Edge image/video interpretation. The listed compute price was $1.4544/hour plus storage. Provisioning, remote GPU/Docker checks, private API access, and one real sample-image inference passed.
- The repository now includes a pinned Docker Compose deployment and a standard-library image client. Three local fake-API protocol tests passed. A real GPU sample-image request completed in 1,168 ms (207 input tokens, 20 output tokens); this is a smoke test, not collision or camera-to-alert validation.
- The owner subsequently requested that compute remain paused until project images are available; Brev confirmed `hackutd-reasoner` is `STOPPED` on November 7, 2026. Retain the instance and model cache, and do not restart compute until the owner is ready to use images. Storage may still incur charges while stopped.
- [Brev notes](brev-reasoner.md) document deployment, tunneling, stopping the instance, and remaining limitations. No camera-to-belt milestone is implied.

## MacBook camera test setup — November 7, 2026

- The owner requested a MacBook-camera test of `hackutd-reasoner` and notice before any action expected to exceed $3. This authorizes a bounded inference test when camera frames are available.
- Added a standard-library local camera page/server: `python3 scripts/webcam_reasoner.py`, then open `http://127.0.0.1:8765` in a Mac browser. Preview does not start compute; the first submitted frame starts the existing VM and a 20-minute test deadline (about $0.49 compute at the recorded rate, plus storage and shutdown time).
- The server requests instance shutdown at the deadline or on inference failure. Before loading the model it requires a second shutdown timer on the VM, which remains unverified until first use. Keep the Mac awake during initial setup.
- The page sends individual frames on demand, reports frame age/latency/tokens, and stores no footage on disk. *(Superseded: completed analyses now save reduced JPEGs in `data/sessions/`; see below.)* This is a development webcam substitute, not Ray-Ban integration or a collision-risk validation.
- Thirteen local protocol/lifecycle tests passed, along with Python compile and whitespace checks. The shared browser reported camera permission as denied. No MacBook-camera frame has been analyzed and the GPU has not been restarted for this setup; `brev ls` confirmed `STOPPED`. The stopped-instance dashboard displayed $0.02/hour for retained storage.

## Camera-session shutdown confirmation — November 7, 2026

- The owner reported that pressing Stop had not yet stopped the GPU. Both `brev ls` and the local server initially reported shutdown pending (`STOPPING` / `stopping`), confirming that the stop request had reached Brev.
- Retried `brev stop hackutd-reasoner` and waited. Brev subsequently confirmed `STOPPED`, and the local server independently reached `stopped` / `finished: true` with no error. Its elapsed-time compute estimate was $0.153; this is not a reconciled billing total. Retained storage may continue billing.
- The session returned no model observation and reported no installed remote shutdown guard; MacBook-image inference and the VM timer remain unverified.
- Corrected the page to show shutdown pending instead of the automatic-stop countdown while a manual stop is underway. Ten camera protocol/lifecycle tests and `git diff --check` passed. The existing server detected shutdown without a restart.

## Automatic frame sampling and perception investigation — November 7, 2026

- The owner requested an automatic sampling loop and investigation of distance estimation, collision-risk software, and belt vibration logic. The earlier $3 action-cost notice preference remains in effect.
- A later saved camera session had completed a real model response: 920 ms request latency, 302 prompt tokens, and 35 completion tokens. Its state reported `remote_shutdown_guard: true` and a finished/stopped session. This supersedes the earlier no-completed-webcam-response notes; it does not establish recognition accuracy, sustained FPS, or independent watchdog behavior under fault injection.
- Implemented **Start live · 1 capture/sec**, **Pause live**, and existing manual capture/stop controls. The browser and server each retain bounded latest-frame state, and the model worker runs one request at a time. New frames replace waiting ones during startup and inference. Captures carry timestamps and server frame IDs; stale, future, and out-of-order uploads are rejected, and expired waiting frames never reach inference.
- The page reports completed FPS, skipped frames, latency, timestamp/age, and explicit unavailable/paused/stale/stopped status. A three-second developer freshness limit also applies at display time; it is not a validated hardware-alert deadline. Camera loss and hidden tabs pause capture. Pause clears waiting work and suppresses older in-flight results, but keeps compute billable until Stop or the unchanged 20-minute deadline.
- This loop sends independent images, not temporally conditioned video. No footage is saved, no training runs, and no belt commands are emitted. Actual sustained webcam/GPU FPS has not been measured with the new loop.
- Added [the depth, collision-risk, and belt investigation](perception-and-haptics.md), based on primary repositories and haptics studies. Proposed first experiment: evaluate Depth Anything V2 Metric Hypersim Small against measured indoor distances, then compare Depth Pro if needed. Metric3D, OpenCV optical flow, ByteTrack, and Nav2 Collision Monitor/Detector are also assessed. No dependency/weights have been installed for these proposals.
- The investigation specifies separating LLM observations from metric geometry, checking camera-to-body orientation before mapping image sides to belt regions, tracking actual capture times, and treating unknown range/closing rate as unknown risk. It sketches body-relative obstacle-direction cues, bounded pulse/cadence candidates, a distinct health state, firmware caps/watchdog, freshness/session validation, and manual-stop priority. These are proposed rules, not a hardware implementation or validated safety policy.
- Passed: **25 Python protocol/lifecycle/queue tests**, **7 JavaScript scheduler/freshness tests**, Python compilation, JavaScript syntax, and whitespace checks. Local HTTP checks returned 200 for the camera page, scheduler, and status; the restarted local server is idle with zero accepted frames. Brev confirmed `hackutd-reasoner` remains `STOPPED`. No GPU restart was performed for this implementation/research.
- Not run: sustained real-camera loop/GPU benchmark, depth-model evaluation, geometric collision-risk validation, haptic bench hardware, and wearable trials. The local page is available at `http://127.0.0.1:8765`; refresh it to load the new controls. Starting live or manually submitting the first frame begins a roughly $0.49 compute session at the recorded rate, plus storage and shutdown time.

## Persistent frame analysis records — November 7, 2026

- The owner explicitly requested persistent storage of frame analyses. Added `scripts/analysis_log.py` and enabled it in the webcam server for both manual and live captures. The standalone image CLI is unchanged.
- *(Superseded: the default is now numbered `data/sessions/session_NNNNNN/` folders; `--analysis-log` keeps this aggregate file optional.)* Completed responses append to `data/analysis/frames.jsonl` by default, with session/frame IDs, capture/completion times (Unix seconds and UTC), model observation/ID, latency, frame age, completion staleness/activity, and token counts. Each record is flushed and synced before acknowledgment. Server restarts append without overwriting older records; a custom file is supported with `--analysis-log`.
- The logger uses an explicit field allowlist. Raw image payloads, request bodies, authorization headers, and arbitrary API metadata are not serialized. The default folder is Git-ignored and the initialized file has owner-only (0600) permissions. Text analyses remain local until manually removed.
- Completed responses suppressed by pause/stop are still logged with their activity state. Replaced/rejected frames and requests without a completed response produce no invented analysis records. Write failure is surfaced, requests GPU shutdown, and keeps any available unsaved response in memory for recovery.
- Passed: **31 Python tests**, **7 JavaScript tests**, Python compilation, JavaScript syntax, and whitespace checks. New temporary-file tests verify append persistence across sessions, text/newline handling, field exclusions, private permissions, successful worker persistence, suppressed-response persistence, and shutdown on write failure. These tests use synthetic responses and never write fabricated analyses into the real report.
- Restarted the inactive local server to enable logging. Its status confirmed logging enabled, zero saved records, and no GPU session started. The real `data/analysis/frames.jsonl` file exists and is empty, ready for the next completed camera analysis. Prior memory-only sessions have not been reconstructed. Refresh `http://127.0.0.1:8765` before continuing.

## Live capture recovery fix — November 7, 2026

- The owner reported Live stopping after one frame. Tests of the real page handlers with simulated camera/HTTP reproduced permanent stops on tab hiding, one failed status poll, a temporarily unready video, and an upload transport failure. The original browser event was not recorded, so these reproductions do not establish which interruption triggered the owner's session.
- Live now preserves its enabled state during those temporary interruptions. Hidden pages discard the local waiting frame and resume sampling automatically on return; unready/muted video skips samples until a current frame is available. Failed status polls and retryable uploads recover automatically while observations are marked unavailable. Rejected authorization, actual camera disconnect, explicit Pause/Stop, and an ended server session still stop capture.
- Added a separate capture count/waiting indicator. Upload/model queues remain bounded, old frames still expire, and the existing 20-minute spending deadline and persistent analysis log are unchanged.
- Passed: **31 Python tests**, **19 JavaScript tests** (scheduler and simulated page handlers), Python compilation, JavaScript syntax, whitespace checks, and HTTP checks for the updated page, script, and idle status. The new page tests failed against the previous code before the recovery fix. They cover repeated submissions, hide/return, status/upload failures, video warmup/muting, explicit Pause/Stop, disconnection, and authorization rejection.
- After Brev confirmed `hackutd-reasoner` STOPPED, restarted only the local server at `http://127.0.0.1:8765`. It reports idle with no submitted frames and logging enabled; the two existing analysis records were preserved. A subsequent Brev listing still showed STOPPED. Refresh the page to load the updated script and local session token.
- Failed checks: none after the fix. Blocked checks: none for the local implementation. Not run: sustained physical-camera capture or paid GPU inference for this fix; all new camera/network regressions use simulations. Starting Live remains the action that starts the paid session.


## Camera preview after GPU failure — November 7, 2026

- The owner reported the camera shutting off after starting the feature and being unable to re-enable it. The local server showed one accepted frame, zero completed analyses, a `resource exhausted` error, and shutdown in progress. Brev independently showed `hackutd-reasoner` STOPPING. The available error text does not establish the underlying resource limit or which setup command failed.
- Reproduced the camera lockout in simulated page tests: every status render for stopping/stopped/stop-error released the camera, including a newly re-enabled camera. Changed that handler to stop the capture/upload loop while preserving the independently controlled local preview. Camera restart in a blocked session does not post pause/analyze requests. Explicit Stop, actual camera disconnect, camera replacement, and page exit still release camera tracks.
- The page now explains that camera preview remains available after the analysis session ends and that another analysis session needs a local-server restart. No automatic paid-session restart was added. The existing server reads updated assets on request, so refreshing loads the fix without interrupting the active shutdown monitor.
- Passed: **31 Python tests**, **21 JavaScript tests**, JavaScript syntax and whitespace checks, and an HTTP check confirming the fixed script is served. The two new camera regressions failed before the fix and pass afterward. Failed checks: none after the fix. Blocked checks: none for the local UI fix. Not run: physical-camera re-enable and paid GPU inference; the resource-exhaustion failure remains unresolved and was not retried.
- Latest cloud verification for this change still showed STOPPING, not confirmed STOPPED. The server continues its existing shutdown checks; compute shutdown is not yet confirmed. No new GPU start was requested while debugging, and existing analysis records were left untouched.


## Real-camera multi-frame analysis verified — November 7, 2026

- The owner authorized GPU restarts and repeated testing until multiple camera frames have analyses, requested leaving the script running regardless of FPS, and requested notice if charges exceed $3.
- Reproduced the actual one-frame scheduler failure in a real browser: storing native `setInterval`/`clearInterval` as methods on the loop changed their receiver, causing `TypeError: Illegal invocation` after the first capture. Fixed both with wrapper functions. The page tests now reject that receiver misuse; they failed before the fix. A native-browser test then sent three simulated frames over 2.2 seconds and stopped its test timer cleanly; that test made no model requests and wrote no analyses.
- Revised startup to reuse running/starting instances, wait for prior shutdown, use detached start, retry Brev resource exhaustion within a bounded operation, and label the failed operation. A dedicated loopback SSH tunnel replaces the CLI forwarding path. Transient inference network/HTTP failures retry the latest frame within the original session/cost deadline. No FPS threshold stops capture or the script. The exact origin of the earlier unlabelled resource-exhaustion error remains unproven; the revised real test ran without it.
- The shared preview browser denied camera permission. Used the owner's normal Chrome camera tab with its existing permission and explicit one-use `#live` link; no browser security setting or OS camera permission was bypassed. `#preview` enables camera preview without inference. Added authorized, memory-only camera health reporting to diagnose real startup separately from inference; it records health event/error name/time, with no footage or scene text.
- Real test evidence: 163 frames accepted, 79 model analyses completed and saved, and 79 distinct frame IDs confirmed in the latest persistent-log session. An active sample reported 0.94 completed analyses/sec, zero inference failures, and no current error. This is a real camera/network/GPU result, not a simulation. Earlier records remain in the append-only ignored report; no raw images were saved.
- The page subsequently sent Pause, then Stop GPU before the deadline. Those controls were honored. At final verification the local Python server was still listening (PID 26334), the GPU session was stopping with no current error, 79 analyses were saved, and the cost guard had 788 seconds remaining. This was not an FPS-triggered stop. Current-session estimated compute was approximately $0.17; the conservative troubleshooting estimate remained below $3 at the displayed ~$1.47/hour. These are estimates, not a final invoice; cloud shutdown was not yet confirmed at handoff.
- Passed: **37 Python tests**, **23 JavaScript tests**, JavaScript syntax, Python compilation, whitespace checks, native-browser timer verification, and physical-camera multi-frame inference/persistence. Failed checks: none after the fixes. Blocked: camera permission in the shared preview browser; normal Chrome succeeded. Not run: Ray-Ban video, metric depth, collision-risk accuracy, belt hardware, or wearable navigation trials.

## Numbered frame recordings and CPU perception baseline — November 7, 2026

- Owner requested saving every analyzed image with its analysis, smaller ordinary
  JPEGs instead of individual archives, consistent numbered sessions, and work on
  tracking/distance. Owner also authorized a reasoner VM restart if testing needs
  it and retained the request to flag actions costing more than $3.
- Added `SessionRecording`: each local server run atomically allocates
  `data/sessions/session_NNNNNN/`, with `session.json`, `frames.jsonl`, a maintained
  observations-only text file, and ordinary JPEGs in `images/`. Pause/resume stays
  in that session. Preview-only starts can create empty folders. Frame numbers
  reflect accepted capture IDs, so skipped/replaced inputs leave gaps.
- The original submitted JPEG goes to inference unchanged. After a completed
  response, Pillow resizes an archive copy to at most 320×240 (quality 75, no EXIF).
  Each version-2 JSONL entry links its JPEG, source/saved dimensions, bytes and
  SHA-256, with the existing allowlisted analysis/timing/usage/activity fields.
  Reduced images precede synced index writes; recording failures are visible and
  request GPU shutdown. Partial failures can leave an unindexed image or extra
  observation; JSONL remains authoritative. Default data paths are Git-ignored;
  owner-only file/directory permissions apply. No automatic deletion was added.
- The 81 existing text-only records and observations export were preserved.
  Past images were never retained and cannot be reconstructed. `--analysis-log`
  remains an optional text aggregate in addition to mandatory numbered recordings.
- Added separate CPU-only replay using a pinned, SHA-256-verified OpenCV Zoo
  NanoDet ONNX and its attributed Apache-2.0 helper. A class-aware greedy IoU
  tracker supplies candidate IDs, image direction, missing states, and timestamp
  expiry. Replay writes numbered perception reports/optional annotated JPEGs.
  Camera motion, occlusion, rapid motion, compression and crossing objects remain
  limitations; general-object range and collision risk are explicitly unknown.
- Added printable checkerboard/ArUco targets, camera-intrinsic fitting, and
  known-size target pose estimation with small/clipped/poor/ambiguous pose gates.
  Intrinsics scale with saved resolution; aspect-ratio mismatch is rejected.
  Same-aspect crops or another camera require manually ensuring the correct
  calibration. Range is explicitly experimental, with no real-distance validation.
  A held-out reference evaluator reports coverage, per-target errors, mean and
  worst absolute errors. No measured real range report exists yet.
- Passed: **49 Python tests**, **23 JavaScript tests**, syntax/compilation,
  dependency consistency and whitespace checks. Tests include synthetic JPEG
  pairing/post-inference resizing, private permissions, concurrent numbering,
  preservation, paused completion and disk failure; class/identity/missing/expiry
  tracking, synthetic marker geometry at two resolutions, unknown-range gates,
  measurement coverage, and an actual downloaded CPU detector processing a
  three-frame synthetic replay (including checksum rejection/numbered outputs).
  These are synthetic/replay results, not new physical-camera/GPU/range evidence.
- Reloaded the completed/idle local server with `.venv/bin/python`; the new
  recording is `session_000001`, with zero model requests at the HTTP check.
  Existing pages need refresh to load the new session token/UI. Confirmed Brev
  `hackutd-reasoner` STOPPED before the reload; this work requested no GPU start.
  No additional Brev compute was used by these tests. Teammate's VM was untouched.
- Failed checks: none after implementation. Blocked checks: none for recording
  and local replay. Not run: new real-camera recording through paid inference,
  real camera calibration/held-out distance trials, general metric depth,
  collision accuracy, Ray-Ban input, belt actuation or walking trials. Previous
  real-camera inference evidence remains documented above, not extended by these
  synthetic tests. Setup and limitations: [session guide](session-recording.md).

## Restarting Live after a completed session — November 7, 2026

- Owner refreshed the camera page and manually restarted the reasoner VM, but
  could not start Live. Reproduced the blocker: local status still had
  `stage: stopped`, `finished: true`, zero completed analyses and 168 accepted
  frames, while Brev independently showed the VM RUNNING. The old app deliberately
  disabled Live for that one-use local session; refreshing or restarting the VM
  did not replace the server's session object. Camera health had reported ready.
- Added a SessionHost that owns the current session, plus authorized
  `/new-session` and **New analysis session** controls. After confirmed prior
  shutdown and worker completion, an explicit reset allocates the next private
  numbered recording, clears counters/deadline/results, and preserves camera
  preview. It does not call Brev or start inference. Active/stopping/in-flight
  sessions cannot be replaced. Disk failure leaves the previous session intact.
  Server shutdown targets the current session after rollover. Mutations carry
  the active session ID; stale pages/late uploads cannot act on a replacement.
  Status responses captured during reset are ignored by the page.
- Restarting the VM manually had not started its reasoner container, and the old
  shutdown timer was inactive. Started the existing deployment and installed a
  20-minute remote shutdown guard. Verified the model API returned cosmos3-edge
  and the container became healthy. The main local server was reloaded only after
  confirming its old session had ended; no active inference was interrupted.
- Passed: **53 Python tests**, **25 JavaScript tests**, syntax/compilation and
  whitespace checks. New cases cover stopped-to-new-to-multiple-live-frame UI
  flow, preview preservation, no GPU action for allocation, blocked rollover
  during work/shutdown, failed allocation, shutdown ownership, and HTTP rejection
  of stale-session analyze/stop/pause/reset requests. Native browser page state
  confirmed the new controls and idle status; a shared-browser snapshot timed out,
  but its read-only page evaluation succeeded.
- Actual GPU smoke test: **three synthetic images** produced three successful
  model responses, paired with three 320×240 JPEGs in an isolated temporary
  numbered recording. Request latencies were 507, 418 and 338 ms in that test.
  Temporary synthetic records were removed; real camera recordings were not
  modified. This establishes current model availability and recording through
  inference, not physical-camera Live or tracking/range accuracy.
- Failed checks: none after fixes. Blocked: shared-browser screenshot automation
  timed out; page-state inspection succeeded. Not run: a new physical-camera
  Live test, Ray-Ban input, real metric range or belt behavior. The VM is left
  running for the owner's requested use, with a remote spending guard. No action
  in this repair was expected to cost over $3; recorded compute is $1.4544/hour
  and a full 20-minute guarded session is approximately $0.49 plus storage.
  These are cost estimates, not a billing invoice.
- Subsequent owner-operated camera session evidence supersedes the not-run camera
  check above: the reloaded main server's `session_000002` accepted 76 frames,
  completed 68 model analyses, and had 68 JSONL records paired with 68 existing
  JPEGs at inspection. No inference/recording error was reported. The local
  session estimated $0.062 compute at that sample, excluding the VM's earlier
  manually started warm-up/storage. An optional final idle-only reload was
  skipped because the owner's session had started; it was left untouched. The
  main script and GPU remain running under the existing guard, without any FPS
  cutoff. No new Ray-Ban, range or belt validation is implied.

## Concise collision assessments with NVIDIA vision — November 7, 2026

- Owner requested collision-focused observations, distinctions between moving
  objects, use of past frames or another temporal method, and continued NVIDIA
  vision. The chosen live baseline keeps Cosmos3-Edge on Brev for object labels
  and boxes, while local OpenCV measures image changes. No CPU recognition model
  replaces NVIDIA in Live; the existing NanoDet replay remains separate.
- A native NVIDIA video experiment was rejected: controlled receding and
  stationary rendered targets were called approaching. Constrained verbose
  schemas also caused duplicate/poor detections. The final compact grounded
  detection schema/prompt was tested with optical flow, duplicate suppression,
  explicit uncertainty and no model-guessed temporal labels. It uses thinking
  disabled and a 320-token cap. No new training was performed.
- Added `collision.py` and `visual_motion.py`: bounded four-frame/five-second
  local context, capture-time intervals, optical flow with forward/backward
  checks, RANSAC affine scale/displacement fits, background camera-motion
  estimation, class-aware candidate tracks, measured-position association,
  approaching/crossing view-space candidates, and explicit missing/unknown states.
  Insufficient features, fast untrackable motion and malformed output stay
  unknown. Camera-relative and independent motion are separate; the latter is
  unknown when background evidence cannot establish a stationary camera.
- The live page and observations-only text now show short possible-collision,
  no-apparent-collision-detected, or unknown summaries. Stale/unavailable results
  expire to unknown in the UI; stale completions persist as unknown with the
  historical candidate state retained. Tracking context resets after pause,
  resolution/generation changes and long capture gaps. No FPS cutoff was added.
- Existing numbered JPEG/JSONL recordings now include an allowlisted collision
  block with candidate IDs, boxes, motion/feature evidence and temporal frame
  references. Range and physical TTC are null; these are experimental image-space
  candidates, not validated collision predictions or belt commands. All previous
  81 text-only and 68 paired-session records were checked intact.
- Passed: **68 Python tests**, **27 JavaScript tests**, compilation/syntax,
  dependency consistency, whitespace and HTTP-serving checks. New tests use real
  OpenCV on rendered targets for expansion/contraction/still/lateral motion,
  moving-camera backgrounds, sparse features and fast movement; policy tests
  distinguish multiple objects, missing hazards, pauses, gaps, stale completions,
  malformed/native grounding output, duplicate IDs and private recording.
- Actual NVIDIA GPU + real optical-flow synthetic sequence results: approach →
  possible, receding → no apparent collision detected, stationary → no apparent
  collision detected, moderate crossing → possible, fast crossing → unknown.
  Final sampled request latency was 917–1599 ms. Evidence and exact limitations:
  [collision guide](collision-check.md) and the synthetic JSON report linked there.
  This is not a real-world accuracy benchmark or a new physical-camera test.
- Actual production worker/recording smoke: two synthetic GPU analyses produced
  two paired JPEG/JSONL records, preserved measured approaching motion, and ended
  with `POSSIBLE COLLISION — circle center, approaching.` Latest frame age was
  1.562 seconds. Temporary synthetic recordings were removed. An initial GPU
  recording smoke assertion failed to find two expected rows near the prior
  test-session shutdown boundary; the exact cause was not established. Retrying
  under a fresh guarded session passed with zero inference failures/expired
  frames and two saved records. No user records were modified by these tests.
- Reloaded the local server only after confirming the previous camera session
  had finished. New app session is `session_000003`; its initial HTTP check was
  idle, with no submitted frames, and served the updated collision page. The
  script remains running. The reasoner is healthy/warm with its remote shutdown
  guard active; the next Live action starts its normal local 20-minute timer.
  Compute used the existing recorded $1.4544/hour rate, two bounded test windows
  were authorized, and no action was expected to exceed $3. This is an estimate,
  not a final invoice; the teammate's instance was untouched.
- Failed/rejected experiments: native model-only temporal judgments and the
  initial smoke assertion described above; final available checks passed.
  Blocked checks: none. Not run: new physical-camera collision assessment,
  multi-object real-camera accuracy, real collision/range validation, Ray-Ban
  capture or belt/walking trials. No apparent collision must not imply clear path.


## GitHub agent handoff — November 7, 2026

- Owner requested publishing all progress so another agent can continue. The
  NVIDIA webcam, private recording, offline perception/calibration, deployment,
  tests and research documentation were captured on
  `nathan/nvidia-collision-handoff`. Integrated `origin/main` through `3e8d956`
  without changing teammate hub, firmware, iOS, bridge or protocol code. The
  `.gitignore` merge retains both camera-data exclusions and teammate build rules.
- Added [agent handoff](agent-handoff.md) with setup, architectural boundaries,
  cost controls, commands, evidence, unresolved work and private-state exclusions.
  Linked it from README. Clarified that historical STOPPED VM notes do not
  establish current power state. The NVIDIA experiment and teammate hub still
  use separate environments and are not connected to one another or the belt.
- After integration, passed 68 Python tests, 27 JavaScript tests, Python compile,
  JavaScript syntax and dependency consistency checks. Removed trailing whitespace
  in the newly introduced vendored README and JS EOF; noted the README-only
  normalization in upstream attribution. Final branch whitespace check is clean.
- Read-only local status found the existing server idle in `session_000003`, with
  zero saved records and no error. Left the script running. No new GPU calls,
  restarts or paid inference were performed for GitHub publication. Current VM
  power and invoice totals were not re-verified.
- Failed checks: initial publication whitespace check found newly tracked
  whitespace; corrected before final verification. Blocked checks: none.
  Not run: teammate component tests or new physical-camera, Ray-Ban, belt,
  walking, collision/range or GPU validation. Prior evidence remains separately
  documented. Credentials, recordings, weights, local account config and caches
  are excluded from the published changes.

## PR #3 review follow-up — November 7, 2026

- Agreed with Nathan; owner approved. Details in [the agent handoff](agent-handoff.md#ports-fixes-and-hub-integration--november-7-2026).
- The NVIDIA webcam page moved from `127.0.0.1:8765` to `127.0.0.1:8770`, and its tunnel's local end from 8000 to 8001, so neither shares a port with the hub (glasses WebSocket 8765; cloud-classifier port-forward 8000). Earlier entries above that mention 8765/8000 describe the old ports.
- Fixed: stale "no apparent collision" text stayed on the page when the local server stopped answering; closing the terminal didn't request a GPU stop; failed stops weren't retried; a narrow tunnel/stop race; loose `brev ls` matching; null or non-JSON model replies ended the session. Verified with local unit tests only (no GPU, Brev, camera or belt).
- Known gap kept open: a lost hazard returns to "no apparent collision" after the 5 s track gap ([collision check](collision-check.md#policy-and-uncertainty)).
- The optical-flow approach/crossing check is being ported into `hub/` behind `--motion flow` (separate branch `chxrro/hub-flow-motion`, synthetic tests only).
