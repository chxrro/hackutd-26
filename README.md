# HackUTD 2026

An accessibility prototype exploring Ray-Ban camera video and a vibration belt to alert blind users to nearby collision risks.

## Project context
- [Agent guide](AGENTS.md): instructions for agents working in this repository.
- [Project context](docs/project-context.md): goals, initial status, open decisions, and proposed milestones.

The project direction is documented; hardware and software integration are not yet verified. Read the context and current code before assuming a working implementation.

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
