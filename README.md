# HackUTD 2026

An accessibility prototype exploring Ray-Ban camera video and a vibration belt to alert blind users to nearby collision risks.

## Project context
- [Agent guide](AGENTS.md): instructions for agents working in this repository.
- [Project context](docs/project-context.md): goals, initial status, open decisions, and proposed milestones.
- [NVIDIA vision agent handoff](docs/agent-handoff.md): current implementation, setup, checks, and next steps.

Hardware and end-to-end camera-to-belt integration are not yet verified. Separately, the NVIDIA webcam experiment in `scripts/` (Brev-hosted Cosmos3-Edge image inference and a MacBook camera multi-frame test) is verified on its own. Read the project context for current status and limitations.

## NVIDIA reasoner setup

See [Brev deployment and credit setup](docs/brev-reasoner.md) for the GPU instance,
private API tunnel, image client, and commands to stop compute billing.

For a MacBook webcam test, install the recording dependency and start the server:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/webcam_reasoner.py
```

Open
`http://127.0.0.1:8770` in Safari or Chrome. Camera preview is local.
**Start live** samples once per second and shows actual analysis FPS. It or
**Capture & analyze** starts a 20-minute GPU session (about $0.49 compute plus
storage). **Pause live** leaves compute billable; use **Stop GPU & camera** when done.
Live resumes after tab switches and retries temporary connection/camera delays;
the capture count shows when new frames are being sampled.
Completed analyses now save ordinary JPEGs (at most 320×240), matching metadata,
and observations in numbered `data/sessions/session_000001/` folders. Images are
reduced **after** inference. Each server run creates a new session; pause/resume
keeps its current folder. After a confirmed stop, click **New analysis session**
to create the next folder and enable Live again without restarting Python.
Enable the camera before clicking Live. Restarting the VM separately does not
reset a finished local analysis session. Private recordings are excluded from Git and retained
until you delete them. Prior text-only logs in `data/analysis/` are preserved.

See [session recording and CPU perception](docs/session-recording.md) for the
folder format, replay commands, calibration targets, and measurement checks.
The live pipeline uses **NVIDIA Cosmos3-Edge for object vision** and measured
OpenCV optical flow for camera-relative motion. Output is now a short possible
collision / no apparent collision / unknown assessment, with per-object candidate
IDs and motion evidence in the saved metadata. See [the collision check](docs/collision-check.md)
for the pipeline, synthetic test evidence and limitations. General metric distance
and physical time-to-collision remain unknown. It does not drive the belt.
The separate CPU replay/calibration tools remain available for experiments.

See the [depth, collision-risk, and belt investigation](docs/perception-and-haptics.md)
for the next perception experiment and proposed vibration rules.

The NVIDIA webcam experiment in `scripts/` is separate from the team hub below.
It has not been connected to the hub or belt. Use separate virtual environments
for these components; their OpenCV dependency variants differ.

## Layout

Three independent sections that talk only through [docs/protocol.md](docs/protocol.md):

| Folder | What | Runs on |
|---|---|---|
| [`esp32/`](esp32/README.md) | belt firmware: BLE in, vibration motors out, failsafe | ESP32 |
| [`hub/`](hub/README.md) | center: video feed in, collision perception, haptic commands out | laptop (Python) |
| [`meta/`](meta/README.md) | phone bridge: Meta glasses camera to the hub | iPhone |

```text
glasses ─▶ meta/ ──WebSocket──▶ hub/ ──BLE──▶ esp32/ ─▶ motors
           (or webcam / video file / RTSP stream straight into hub/)
```

Quick start without any hardware: `cd hub && uv sync --extra vision && uv run haptic-hub run --source webcam --belt sim --show`

## Camera diagnostic

[Camera Bridge](docs/camera-bridge.md) is a standalone iPhone and Mac diagnostic for USB screen mirroring and local browser preview. The USB preview passed with a simulated test pattern. The live Ray-Ban test is pending an update to the app component on the glasses.

## Team
- [@chxrro](https://github.com/chxrro)
- [@NathanS11](https://github.com/NathanS11)
- [@wxu012](https://github.com/wxu012)
- [@minjuna77](https://github.com/minjuna77)

## Getting started
```bash
git clone https://github.com/chxrro/hackutd-26.git
cd hackutd-26
```
