# Cosmos3-Edge reasoner on NVIDIA Brev

Setup started on November 7, 2026. The user authorized Brev account setup,
redemption of hackathon credits, and a GPU deployment for the reasoner.

## Current handoff

The setup and shutdown notes below describe earlier milestones. Later owner-authorized
webcam and collision tests restarted the VM; do not infer its current power state
from an earlier **STOPPED** entry. See [the agent handoff](agent-handoff.md) and
[the latest project context](project-context.md). Check `brev ls` before starting
or stopping compute. Private session data and account credentials are not in Git.

## Account and credits

- Brev CLI: `v0.6.337`, installed at `~/.local/bin/brev`; `~/.local/bin` was added to zsh's PATH.
- NVIDIA/Brev login and active-organization access succeeded.
- The hackathon code was redeemed through a private secret input. The billing
  dashboard then showed **$60.00** in credit.
- No payment card was added and auto-recharge was not enabled.
- Tokens, SSH keys, and the credit code are not stored in this repository.

Credits pay for the rented Brev GPU instance and storage. They do not establish
access to a separate NVIDIA-hosted model API.

## Instance

- Name: `hackutd-reasoner`
- Instance ID: `u7w9ge4yi`
- Cloud/type: AWS `g5.2xlarge`
- GPU: one A10G, 24 GB VRAM; 32 GB host RAM
- Requested disk: 128 GB
- Listed compute rate at creation: **$1.4544/hour**. The search listing priced disk
  at **$0.11972/GB/month**; inspect actual billing for the final storage charge.
- **STOPPED**, confirmed by `brev ls` on November 7, 2026, at the owner's request to conserve credits until project images are available. Retain the instance and downloaded model cache. Do not restart compute until the owner is ready to use images.
- Before shutdown, the GPU, Docker runtime, private tunnel, model discovery, and a real sample-image inference all passed. The console displayed approximately **$1.47/hour** including its listed storage allocation.

Stopping the container does **not** stop GPU billing. Stop the Brev instance:

```bash
brev stop hackutd-reasoner
```

Storage can continue to incur charges while the instance is stopped. Deleting an
instance deletes its data; export needed work first. No automatic account
recharge is configured.

## Deployment

The checked-in Compose deployment uses NVIDIA's documented Cosmos-compatible
`vllm/vllm-openai:cosmos3` image. Its Linux x86_64 manifest and model revision are
pinned in `deploy/reasoner/compose.yaml`.

The initial configuration uses BF16, an 8,192-token context, one concurrent
sequence, limited image pixels/video frames, and disabled CUDA graph capture.
These are prototype resource limits, not measured accuracy or latency claims.
The image client disables thinking to request short observations.

The model port is bound to the instance's loopback interface. Keep it private
and use SSH forwarding for development.

On a ready instance:

```bash
brev refresh
brev exec hackutd-reasoner 'mkdir -p /home/ubuntu/workspace/hackutd-26'
brev copy deploy/reasoner hackutd-reasoner:/home/ubuntu/workspace/hackutd-26/reasoner
brev exec hackutd-reasoner 'cd /home/ubuntu/workspace/hackutd-26/reasoner && bash start.sh'
```

The selected VM supports Docker without `sudo`. Use the standard Brev connection;
the CLI's separate host alias failed to connect during setup. Follow model loading:

```bash
brev exec hackutd-reasoner 'cd /home/ubuntu/workspace/hackutd-26/reasoner && docker compose logs --tail=80'
```

First launch downloads the image and model. A running container is not proof
that the API is ready; wait for its health check to pass.

## Connect from the Mac

Create the SSH tunnel from the Mac. The local end is 8001 because the hub's
cloud classifier documents 8000 for its own port-forward; the VM side stays 8000:

```bash
brev port-forward hackutd-reasoner -p 8001:8000
```

Then, from the project root:

```bash
python3 scripts/reasoner.py health
python3 scripts/reasoner.py image /absolute/path/to/an/image.jpg
```

The client uses Python's standard library. It discovers the model ID, sends
an inline image, reports request latency and token usage, and rejects truncated
responses. It does not save images or drive belt motors.

A sample-image response establishes only that image inference works. It does
not validate real Ray-Ban capture, collision detection, metric distance,
phone networking, latency under load, or belt behavior.

## MacBook webcam test

From the project root, run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/webcam_reasoner.py
```

Open `http://127.0.0.1:8770` in Safari or Chrome on the MacBook (moved from 8765,
the hub's glasses WebSocket port). Refresh an already
open page after updating/restarting the server, then click **Enable MacBook
camera**, allow access, and select the built-in camera if needed. The shared T3
preview reported camera permission denied during initial setup; use a browser
with camera permission on the Mac.

- **Start live · 1 capture/sec** samples a JPEG immediately and every second.
  The first submitted frame starts the existing `hackutd-reasoner` VM and the
  20-minute spending timer. The model's actual completed FPS is displayed;
  one capture per second is a target, not a promise of one analysis per second.
- **Capture & analyze** sends one manual frame when live mode is paused/off.
- **Pause live** discards waiting frames and suppresses results from an in-flight
  request. The GPU remains warm and billable; the session deadline is unchanged.
- **Stop GPU & camera** stops sampling, releases the camera, clears waiting work,
  requests `brev stop`, and waits for cloud confirmation. Shutdown is asynchronous;
  `STOPPING` may last several minutes before `STOPPED`.

One model request is in flight with at most one waiting frame. New captures
replace that waiting frame, including during VM/model startup. A slower local
upload also retains at most one latest capture. Expired waiting frames are
discarded instead of replayed. Completed analyses save reduced JPEGs and metadata
in private numbered session folders; rejected/replaced frames are not recorded.
Audio is disabled. Each image request is independent; the loop does not yet
track objects or feed a sequence/video clip to a temporal model.

A hidden tab suspends sampling and discards the local waiting frame, but keeps
Live enabled. Returning to the page resumes with a fresh frame automatically.
Temporary camera warmup/muting, failed status polls, upload timeouts, and temporary
HTTP errors retry without turning Live off. The page marks inference unavailable
during connection failures and shows a separate live capture count/waiting state.
A disconnected camera or rejected authorization stops Live; reconnect or refresh
before starting again. Browser timers are invoked through wrappers so their native
Window receiver is preserved; directly storing `setInterval` on the loop object
caused Chrome's `Illegal invocation` after the first frame. Pause and Stop remain explicit and never auto-resume.
Closing the browser stops local capture but does not immediately stop compute. Keep the Mac awake and the local server open during
startup; use Stop when done. Temporary transport errors and HTTP 408/429/5xx
inference failures discard the failed image and retry with the latest fresh frame;
previous observations are marked unavailable during retry. Other fatal failures
still request shutdown and disable analysis for that session. Local camera preview stays
available, including while the VM is stopping or stopped. Enable/restart the
camera for preview without starting inference; the page no longer releases it
on each GPU status poll. The explicit Stop GPU & camera button still releases
it immediately. After confirmed stop, click **New analysis session** before another analysis session.

Startup reuses RUNNING/STARTING instances, waits through an existing STOPPING
transition, and requests a detached start when needed. Brev commands retry
`resource exhausted` up to four attempts without cycling the instance; errors
include the failed operation. The local server owns a dedicated loopback SSH
forward instead of reusing Brev's multiplexed forwarding channel. The original
cloud error did not identify its operation, so its exact source remains unknown;
the successful live test below used this revised path without that error.

The explicit one-use URL fragment `#live` enables the camera and starts Live in a
normal Mac browser; it can begin billing. `#preview` enables preview only. These
links still require browser/macOS camera permission and consume their fragment
after loading. Normal page loads require the existing buttons. The authorized
local `/camera-status` endpoint reports only health events and error names in
memory; it does not collect images, scene text, or persistent camera telemetry.

The page reports completed inference rate, skipped frames, capture timestamp,
frame age, latency, and token counts. It explicitly labels unavailable, paused,
stale, and stopped observations. Its **three-second freshness limit** is a
prototype developer setting, not an acceptable hardware-alert delay. A result
that ages past that limit becomes stale even without a new result. The initial
frame is not automatically analyzed minutes later after startup; the worker
selects the latest available frame and rejects an expired one.

### Persistent session recordings

The webcam server now records each completed analysis and a reduced JPEG under
`data/sessions/session_NNNNNN/`. The JPEG is reduced after inference to at most
320×240 at quality 75, with no EXIF. Ordinary files require no extraction.
Each server start or explicit **New analysis session** allocates the next session
number; preview-only runs can be empty. Pause/resume stays in the same session. The page shows the index path and saved
count. See [the session guide](session-recording.md) for the complete schema,
recording guarantees, CPU replay, calibration and checks.

The old `data/analysis/frames.jsonl` and observations export remain intact; those
past text-only records have no recoverable images. To keep appending a text-only
aggregate in addition to the numbered recordings:

```bash
.venv/bin/python scripts/webcam_reasoner.py --analysis-log data/analysis/frames.jsonl
```

Use `--sessions-dir /absolute/private/folder` to change the recording root.
Custom locations are your responsibility to keep out of Git. Files are private
(owner read/write) and session directories are owner-only. Recordings contain
private camera scenes and remain until manually removed. No auth headers,
request bodies, or arbitrary API response fields are serialized.

Completed responses after pause/stop are still saved with
`active_at_completion: false`. Replaced/rejected frames and failed inference
requests do not produce fabricated analysis records. Each saved JPEG is installed
before its JSONL index entry; files are synced before acknowledgement. Save
failure is visible and requests GPU shutdown, preserving the available observation
in memory. A crash/disk error can leave an unindexed image or extra observation;
`frames.jsonl` is authoritative for complete records.

Thinking remains disabled. The live collision pipeline now requests bounded
object detections (320 output tokens) from NVIDIA and combines them with measured
local optical flow and tracking. See [the collision check](collision-check.md).
Calibrated target range remains separate CPU replay. No belt is driven.

The **20-minute deadline includes VM/model startup**. At the recorded compute
rate, this is about **$0.49 compute**, plus storage and shutdown time. The
stopped-instance dashboard showed **$0.02/hour** on November 7, 2026. No action
in this test is expected to exceed $3; about two hours of continuous compute
approaches that threshold. These estimates are not a billing cap.

Before loading the model, the server requires a systemd shutdown timer on the VM
so Mac sleep after installation does not disable that second timer. A later
successful camera session reported `remote_shutdown_guard: true` and ended with
`STOPPED`; independent sleep/fault-injection testing of that timer remains undone.
The local watchdog also requests shutdown at the deadline and confirms cloud
state. Keep the Mac awake during initial setup, before the VM timer is installed.

Wait for `Brev confirmed STOPPED` before assuming compute has stopped. Ctrl+C,
SIGTERM, or closing the terminal (SIGHUP) requests a stop; a failed stop is retried
every 30 s while the server runs. A hard kill, crash or Mac sleep before the VM timer
is installed is not covered: the VM keeps running until stopped manually. If shutdown
reports an error:

```bash
brev stop hackutd-reasoner
brev ls
```

After confirmed shutdown and completion of the previous worker, click **New
analysis session**, enable the camera if needed, then **Start live**. This creates
a new numbered recording and timer without restarting Python. Creating the
recording itself does not start compute. A VM restarted separately can be billable
even while the old local session says ended; its restart does not reset the app.
Storage remains billable while stopped.

Local tests (no GPU, camera, or model weights):

```bash
.venv/bin/python -m unittest discover -s tests -v
node --test tests/test_webcam_loop.cjs tests/test_webcam_page.cjs
node --check scripts/webcam_reasoner.js
```

Node is needed only for the JavaScript test runner, not for serving/using the
page. Protocol, queue, scheduler, and simulated page tests do not validate sustained real-camera
FPS or collision safety.

## Restart after stopping

```bash
brev start hackutd-reasoner
brev refresh
brev exec hackutd-reasoner 'cd /home/ubuntu/workspace/hackutd-26/reasoner && bash start.sh'
brev port-forward hackutd-reasoner -p 8001:8000
```

Container auto-restart is disabled so failed model loading cannot cause an
unattended restart loop. The downloaded model cache lives next to the Compose
file on the VM and is ignored by Git.

## Checks

Local protocol tests exercise a fake HTTP server; they do not run the model:

```bash
.venv/bin/python -m unittest discover -s tests -v
docker compose -f deploy/reasoner/compose.yaml config --quiet
bash -n deploy/reasoner/start.sh
```

## Sources

- [Brev CLI setup](https://docs.nvidia.com/brev/latest/cli/getting-started)
- [Brev documentation](https://docs.nvidia.com/brev/latest/getting-started/overview)
- [NVIDIA Cosmos3-Edge model card and vLLM instructions](https://huggingface.co/nvidia/Cosmos3-Edge)

## Verified results — November 7, 2026

- Brev instance reached RUNNING / COMPLETED / READY / HEALTHY.
- Remote runtime reported NVIDIA A10G (23,028 MiB), driver 595.112.07,
  Docker 29.9.3, and Compose v5.7.0.
- Model: `cosmos3-edge`, served from the pinned NVIDIA Cosmos3-Edge checkpoint.
- Local SSH tunnel listened on loopback port 8000 at the time (now 8001). `/health` returned HTTP 200
  and `/v1/models` returned the expected model.
- A single request using NVIDIA's example image returned:
  “Three vases of different colors and a flower are displayed on a robotic
  arm's workstation.”
- That request measured **1,168 ms** from the client, with **207 prompt tokens**
  and **20 completion tokens**. This is one smoke test, not an end-to-end camera
  latency benchmark or collision-detection evaluation.
- Sample source: `assets/example_reasoning_input.png` from the pinned model
  repository. It was downloaded to a temporary local path, not committed.
- Three local fake-API protocol tests passed; Compose validation and shell
  syntax checks passed.
- A later saved camera-session response measured **920 ms** with **302 prompt
  tokens / 35 completion tokens**. The server reported an installed remote
  shutdown guard and a finished/stopped session. This is one response, not a
  sustained FPS or collision-risk benchmark; it supersedes the earlier statement
  that no webcam response had completed.
- Real Ray-Ban frames, iPhone transport, video inference, collision-risk logic,
  and belt hardware were not tested.
- The owner subsequently requested a pause until project images are available.
  `brev stop hackutd-reasoner` completed; `brev ls` confirmed `STOPPED`.
  The local port 8000 tunnel is no longer listening. Storage may still incur
  charges while the instance is stopped; do not restart compute until requested.

The iPhone DAT capture app remains a separate component. Do not put Brev account
credentials in that app. Connect it through an authenticated HTTPS application
backend when the private inference service has been verified.


## Verified multi-frame MacBook test — November 7, 2026

A real browser reproduced `Illegal invocation` from the original timer binding;
the corrected loop made three simulated sends over 2.2 seconds using the native
browser timers. A separate test in normal Chrome then captured the actual camera,
submitted 163 frames, completed and persisted 79 distinct frame analyses, and
reported about 0.94 completed analyses/sec in an active sample. No inference
failures were counted in that run. The rate is one measured run, not a guarantee.

The page subsequently sent Pause, then Stop GPU before the deadline; these
controls were honored. The local Python server remained listening, and the GPU
session was stopping at final verification. No FPS threshold stops the script.
The original 20-minute local/VM cost guard remains enabled. The current session's
compute estimate was about $0.17 at verification; the conservative estimate for
this troubleshooting run remained below $3. Estimates use the displayed
~$1.47/hour and are not an invoice. The two prior report records were preserved.
No camera footage was saved.

Passed: 37 Python tests, 23 JavaScript tests, Python compilation, JavaScript
syntax, whitespace checks, real-browser timer verification, and real-camera
multi-frame inference/persistence. No belt or collision/distance validation was
performed by this test.
