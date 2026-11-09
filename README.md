# Haptic Belt

[![CI](https://github.com/chxrro/wayfeel/actions/workflows/ci.yml/badge.svg)](https://github.com/chxrro/wayfeel/actions/workflows/ci.yml)

A wearable collision-alert prototype for blind and low-vision users, built at HackUTD 2026. Ray-Ban Meta glasses stream the wearer's view to an iPhone, a laptop on the same network estimates collision risk in five directions, and five vibration motors on a belt tell the wearer where the obstacles are.

<p align="center">
  <img src="docs/images/belt-front.jpg" alt="Front view of the belt: three coin vibration motors circled on a leather belt, wired to a purple 3D-printed control enclosure on the right hip." width="100%">
  <br><sub><b>Fig. 1.</b> The belt. Coin vibration motors sit along the strap; the harness runs in a sleeve to the control enclosure on the right hip (lid removed for the photo).</sub>
</p>

> [!WARNING]
> This is an experimental hackathon prototype, not a mobility aid. It must not replace a cane, guide dog or orientation-and-mobility training. Distance, time-to-collision and detection accuracy are unvalidated. Use it only seated, standing, or in a supervised bench setting.

## How it works

The glasses stream video to the iPhone through Meta's Wearables SDK. The phone forwards JPEG frames over a WebSocket to a laptop, which runs a monocular metric-depth model and a tracked object detector, fuses them into a risk value for each of five directions, and sends the resulting motor levels back to the phone. The phone relays them over Bluetooth LE to an ESP32 on the belt, which drives one motor per direction, from the wearer's far left to far right.

A few rules shaped the design. A vibration means there is an obstacle in that direction; it never means "move that way". Silence is never treated as a clear path: stale frames, a stalled model, a dropped phone or a lost Bluetooth link all switch the belt to an explicit fault pattern. Every motor command expires after 250 ms unless a fresh one arrives, so a crashed laptop or phone cannot leave a motor running. Both models run locally on the laptop (Apple MPS or CUDA), and no frames leave the local network in the default configuration.

<p align="center">
  <img src="docs/images/architecture.png" alt="Topology: glasses send video to the iPhone (1); the iPhone sends JPEG frames to the laptop (2) and receives motor levels back (3) over WebSocket port 8765; the iPhone sends BLE commands with a 250 ms TTL to the ESP32 (4), which reports status back; the ESP32 drives five coin ERM motors through two ULN2003 drivers with PWM (5)." width="100%">
  <br><sub><b>Fig. 2.</b> System topology. Numbers follow the data through one cycle; the alert path is drawn in purple.</sub>
</p>

| # | Link | Transport | Payload | Rate / bound |
|---|---|---|---|---|
| 1 | Glasses → iPhone | Meta Wearables Device Access Toolkit 1.0.1 | raw frames, 504 × 896 | 15 fps requested |
| 2 | iPhone → laptop | WebSocket, `ws://<laptop>:8765/` | `HB` v1 binary: 20-byte header + JPEG (q 0.6) | ≤ 15 fps, newest frame wins, never queued |
| 3 | Laptop → iPhone | same WebSocket, text | status JSON: `mode`, `levels[5]`, `healthy`, `reason`, `hazards` | ~5 Hz |
| 4 | iPhone → ESP32 | BLE GATT write-with-response | 11-byte v1 command | ≤ 15 Hz, one write in flight, TTL 250 ms |
| – | ESP32 → iPhone | BLE notify | 20-byte status: applied `seq`, mode, levels, build id | every 250 ms and on each mode change |
| 5 | ESP32 → motors | LEDC PWM on GPIO 25, 26, 27, 32, 33 | 8-bit duty per motor, via ULN2003 | 5 kHz |

The phone is the Bluetooth central in this setup. The laptop can own the belt link instead (`haptic-hub run --belt ble`), but only one central may be connected at a time.

### Perception

<p align="center">
  <img src="docs/images/hub-pipeline.png" alt="Hub pipeline: frame buffer feeds Depth Anything V2 and YOLO11n with ByteTrack; their risks merge in sector fusion, then the haptic policy and a safety gate that outputs levels or a fault." width="100%">
  <br><sub><b>Fig. 3.</b> Processing on the laptop, in <a href="hub/README.md"><code>hub/haptic_hub</code></a>.</sub>
</p>

The hub keeps only the newest frame and runs two branches on it. For static obstacles, [Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2) (*Metric Indoor Small*, or the outdoor variant with `--outdoor`) predicts metres per pixel; each of five vertical strips takes the 5th-percentile depth in the middle band of the image, and risk ramps from 0 at 2.5 m to 1 at 0.6 m. For approaching objects, [YOLO11n](https://docs.ultralytics.com/models/yolo11/) with ByteTrack follows each object, time-to-collision is estimated from box growth (TTC ≈ h / (dh/dt), fitted over 0.6 s), and risk ramps from 0 at 3 s to 1 at 1 s across the strips the box covers.

Each sector takes the larger of the two risks. Optional Lucas–Kanade optical flow (`--motion flow`) can only raise it. The haptic policy applies rising risk immediately, lets falling risk decay, ignores values below a deadband, and maps the result to PWM 90–255. A safety gate replaces the levels with the fault pattern when no frame has arrived for 1 s, no result for 1.5 s, the result came from a frame older than 1 s, or a model raised an exception. All of these thresholds are defaults in `config.py`; none has been validated against measured distances.

Two alternatives are opt-in: `--detector openvocab` swaps YOLO11n for text-prompted [YOLOE](https://docs.ultralytics.com/models/yoloe/) to catch poles, bollards, stairs and curbs, and `--alert-profile stable` requires three frames over 200 ms before a moderate alert fires. The [hub README](hub/README.md) has timings and trade-offs.

## Demo

<table>
<tr>
<td width="300" valign="top">
<img src="docs/media/yolo-demo.gif" alt="Hub renderer output: indoor corridor seen from the glasses with person and object boxes, five sector columns, per-sector distance and TTC estimates, and motor-level bars." width="288">
</td>
<td valign="top">

The hub's debug view while streaming live from the glasses, with real models on Apple MPS (10 s excerpt; [full 60 s MP4](docs/media/yolo-demo.mp4)). Phone haptics were on and no physical belt was connected, so the bars show the levels the hub computed. Bystanders' faces are blurred.

| Overlay | Meaning |
|---|---|
| `LEVELS 31 ms [glasses]` | belt mode, inference time, frame source |
| magenta / orange box | person / object, with ByteTrack ID |
| white columns | the five sector boundaries |
| `5.1m 2.6s` | per-sector depth and TTC estimate |
| red bars | level sent toward each motor |
| `9.8fps … tot 82 ms` | hub input rate and per-stage latency |

Boxes and distances are model outputs, not ground truth.

</td>
</tr>
</table>

## Hardware

<p align="center">
  <img src="docs/images/belt-side.jpg" alt="Side view of the belt with two coin vibration motors circled on the wearer's left side." width="640">
  <br><sub><b>Fig. 4.</b> Left side of the belt. The motors continue around the waist from the front view in Fig. 1.</sub>
</p>

| Qty | Part | Role |
|---:|---|---|
| 1 | Ray-Ban Meta (Gen 2) glasses | camera; Developer Mode enabled in Meta AI |
| 1 | iPhone (tested: iPhone 17, iOS 27.2) | frame uplink and BLE relay |
| 1 | Apple-silicon laptop (tested: MacBook, MPS) | local inference |
| 1 | ESP32 DevKit V1, 30-pin, USB-C | BLE peripheral, PWM, command watchdog |
| 2 | Inland ULN2003 driver boards | low-side switch, one channel per motor |
| 5 | Generic coin ERM vibration motors | haptic output |
| 1 | 470 µF electrolytic capacitor | bulk decoupling on the 5 V rail |
| 1 | Veektomx MagSafe power bank, built-in USB-C | only power input, through the ESP32's USB-C |
| 2 | 5-wire JST-XH stepper cables + ribbon extension | motor harness, cut and spliced |
| 1 | 3D-printed enclosure (base, lid, clip) | [`cad/belt-enclosure`](cad/belt-enclosure/README.md) |
| 1 | Leather belt | motor carrier |

### Enclosure

<p align="center">
  <img src="docs/images/enclosure-photo-vs-cad.jpg" alt="Left: the printed enclosure with the lid removed. Right: the OpenSCAD model of the same enclosure. Numbered markers 1 to 4 identify the same parts in both." width="100%">
  <br><sub><b>Fig. 5.</b> The enclosure as printed (left) and as modeled (right). 1 ESP32 DevKit V1 · 2 ULN2003 drivers and jumper harness · 3 motor harness exit · 4 USB-C power input.</sub>
</p>

The enclosure is a parametric [OpenSCAD model](cad/belt-enclosure/belt_enclosure.scad) with every dimension exposed as a named parameter. `./build.sh` exports print-ready STL and 3MF files (base, lid and clip on one 233 × 168 mm plate, no supports), renders previews, and fails if any two parts overlap by 0.02 mm or more. Several dimensions were estimated from photos before the parts were measured and are marked `MEASURE` in the source.

<table>
<tr>
<td width="38%"><img src="docs/images/cad-exploded.png" alt="Exploded CAD view: power bank above the lid and its steel ring pocket, above the base holding the ESP32 and driver boards."></td>
<td valign="top">

| Property | Value |
|---|---|
| Outer size (default parameters) | 112.3 × 78 × 35.5 mm, 132.3 mm with belt ears |
| Stack-up | webbing, base, ULN2003 on 4 mm rails and ESP32 on 20 mm pillars, lid, MagSafe ring, power bank |
| Openings | USB-C notch; two harness notches clamped by the lid |
| Fasteners | 4 × M3 × 10 for the lid; 2 × M3 with captive nuts for the clip |
| Print | PETG preferred, 0.2 mm layers, 4 walls, 20–25 % infill |

</td>
</tr>
</table>

### Wiring

<p align="center">
  <img src="docs/images/belt-wiring.png" alt="Schematic: power bank into the ESP32's USB input; VIN is the +5 V net with C1 470 microfarads to ground; GPIO25, 26, 27 and 32 drive U2 inputs 1 to 4 and GPIO33 drives U3 input 1; driver outputs A to D sink motors M0 to M3 and U3 output A sinks M4; each motor's other lead is on +5 V; unused U3 channels are not connected." width="860">
  <br><sub><b>Fig. 6.</b> Belt schematic. U2 and U3 are the two ULN2003 boards; their unused channels are cut and insulated.</sub>
</p>

| Motor | Position | ESP32 pin | Driver channel | Harness wire |
|---|---|---|---|---|
| M0 | far left | GPIO25 | U2 IN1 → A | blue |
| M1 | left | GPIO26 | U2 IN2 → B | pink |
| M2 | center | GPIO27 | U2 IN3 → C | yellow |
| M3 | right | GPIO32 | U2 IN4 → D | orange |
| M4 | far right | GPIO33 | U3 IN1 → A | blue |

Each ULN2003 channel sinks one motor's negative lead while every positive lead sits on the shared 5 V wire, so each motor's strength follows its own PWM duty, capped by `kMaxDuty` in [`esp32/include/config.h`](esp32/include/config.h). The pins avoid the ESP32's strapping pins (0, 2, 12, 15) and input-only pins (34–39). The physical order has to match the firmware or alerts point the wrong way; check it with `haptic-hub bench --only motor-map` or the app's per-motor test. Rail voltage and motor current have not been measured.

### What the wearer feels

| Situation | Belt output |
|---|---|
| Boot, before a central connects | self-test sweep left to right, then silent |
| Obstacle risk in one or more sectors | matching motors vibrate, stronger as risk rises |
| No risk above threshold | silent, which does not mean the path is clear |
| Hub cannot vouch for its output | double pulse on all motors every 2 s |
| No valid command within the TTL, or BLE lost | motors stop, fault pattern for 30 s, then dormant |
| Pause, resume, session ended (laptop-owned link) | outside-in cue, inside-out cue, three fading pulses; through the iPhone relay these arrive as a fault |

This logic lives in [`esp32/lib/belt_core`](esp32/lib/belt_core/src), plain C++ with no Arduino dependency, so the same code runs in the firmware, in host unit tests and inside the hub's simulator.

## Wire protocol

The three components share one contract, [`docs/protocol.md`](docs/protocol.md). Integers are little-endian, every message carries a version byte, and golden vectors are checked by tests in `hub/`, `esp32/` and `meta/`.

Frame packet, phone to laptop (WebSocket binary):

```text
 0      2    3     4           8                16       18       20
 ┌──────┬────┬─────┬───────────┬────────────────┬────────┬────────┬──────────┐
 │ "HB" │ v1 │ flg │ seq u32   │ capture_ms u64 │ w u16  │ h u16  │ JPEG …   │
 └──────┴────┴─────┴───────────┴────────────────┴────────┴────────┴──────────┘
```

Haptic command, central to belt (BLE write to `71dbf1b6-…-b8a38e54618f`):

```text
 0      1      2     3            5       6
 ┌──────┬──────┬─────┬────────────┬───────┬────────────────────┐
 │ v1   │ mode │ seq │ ttl_ms u16 │ count │ levels[count] u8   │
 └──────┴──────┴─────┴────────────┴───────┴────────────────────┘
 mode: 0 off · 1 levels · 2 fault        ttl clamped to 50–1000 ms

 golden  01 01 07 fa 00 05 00 40 80 c0 ff
         levels · seq 7 · ttl 250 · [0, 64, 128, 192, 255]
```

The belt also exposes an info characteristic (protocol version, motor count and build id) and a status characteristic with the last applied sequence number, its age and the live motor levels. Centrals use the status to tell "the radio accepted the write" apart from "the firmware is following it".

## Repository layout

| Path | Contents | Runs on |
|---|---|---|
| [`hub/`](hub/README.md) | perception, haptic policy, safety monitor, BLE and simulated belt, dashboard, recorder, simulator | laptop, Python 3.11+ |
| [`esp32/`](esp32/README.md) | NimBLE GATT server, PWM motor driver, TTL and fault logic, serial bench console | ESP32, PlatformIO |
| [`meta/`](meta/README.md) | HapticBelt iOS app: glasses stream, hub client, BLE belt relay, speech, phone haptics | iPhone, Swift |
| [`ios/`](docs/camera-bridge.md) | Camera Bridge, the earlier streaming diagnostic | iPhone |
| [`desktop/`](desktop/viewer.py) | browser viewer for Camera Bridge | Mac |
| [`cad/`](cad/belt-enclosure/README.md) | parametric enclosure and build script | OpenSCAD |
| [`docs/`](docs) | protocol, hardware bring-up runbook, glasses setup, navigation research, project log | |

## Getting started

To try the hub without any hardware, install [uv](https://docs.astral.sh/uv/). The first real run downloads Depth Anything V2 Small (~100 MB) and `yolo11n.pt` (~6 MB).

```bash
cd hub
uv sync --extra vision
uv run haptic-hub run --source webcam --belt sim --show          # laptop webcam, simulated belt
uv run haptic-hub run --source video:walk.mp4 --belt sim --show  # a recorded clip
uv run python -m haptic_hub.sim                                  # scripted end-to-end scenarios
```

The full loop needs the belt, the glasses and the phone:

```bash
# 1. Belt firmware, ESP32 on USB
cd esp32
pio test -e native             # host-side logic tests
pio run -e esp32dev -t upload  # build and flash
pio device monitor             # 115200 baud; boots with a left-to-right self-test

# 2. Hub with real perception; the phone owns the BLE link
cd hub
uv run haptic-hub run --source glasses --perception vision --belt sim --device mps --show
#   prints the address to enter in the app, e.g. ws://192.168.x.y:8765

# 3. iPhone app
cd meta
cp Config/Local.xcconfig.example Config/Local.xcconfig   # set DEVELOPMENT_TEAM once
xcodegen generate && open HapticBelt.xcodeproj           # run on a physical iPhone
```

In the app, connect the glasses through Meta AI, start the camera and enter the hub address. Then, under **ESP32 belt · phone Bluetooth**, pick the belt and test each motor before enabling **Send alerts to belt**. [`meta/README.md`](meta/README.md) and [`docs/meta-setup.md`](docs/meta-setup.md) cover glasses Developer Mode, networking and troubleshooting; [`docs/hardware-bringup.md`](docs/hardware-bringup.md) has hardware-day checks with pass/fail criteria.

To rebuild the enclosure:

```bash
cd cad/belt-enclosure
./build.sh                                 # STL/3MF, renders, interference checks
./build.sh -D bank_l=98.4 -D bank_w=64.2   # override measured dimensions
```

## Measured results

*Hardware* means a real phone, glasses or belt; *replayed* means recorded or still images through the real pipeline; *simulation* means the real hub in a scripted world.

| Measurement | Result | Evidence |
|---|---|---|
| Glasses → laptop frame rate, after the phone's rate-limiter fix | 12.0–12.6 fps over four 5 s windows | hardware, 2026-11-08 |
| Hub on live glasses frames, depth + YOLO11n, Apple MPS | 12.6 fps in, 12.4 fps perception; inference 33 ms median, 36 ms p90 | hardware, one 5 s window |
| YOLO11n `track()` alone, M4 Max | 9.2 ms median, 11.3 ms p90 | replayed stills |
| Full `vision` step, depth + YOLO11n, M4 Max | 40.4 ms median, 45.7 ms p90 (15 Hz budget: 67 ms) | replayed stills |
| iPhone → ESP32 BLE heartbeat, alerts off | 1,659 commands over ~130 s; no TTL expiries; max gap 180 ms; max firmware queue delay 7 ms | hardware, belt on USB |
| End-to-end scenarios: direction, approach, head motion, stale frames, disconnects, model crash, lost BLE writes | 24 of 24 pass; each check shown to fail on a deliberately broken hub | simulation, ground-truth stand-ins for the models |

CI runs the hub's `pytest` suite, the firmware's native tests and a compile-only firmware build on every pull request. It does not load models or touch a camera, Bluetooth or the belt, and the iOS app is tested locally on the simulator rather than in CI.

Not yet measured: detection, distance and TTC accuracy; capture-to-vibration latency; Bluetooth reliability under full streaming load; and how well a wearer can tell the five motors apart.

## Limitations

The camera is on the head and the belt is on the torso, so directions are relative to where the wearer is looking, not where the body faces. Low obstacles, curbs, drop-offs, thin poles and glass are largely missed by both depth and detection in our tests. Box-growth TTC reads about 0.35 s late in simulation, and a static wall or pole produces little box growth until it is close.

Bluetooth delivery is not fully characterized. Isolated command expiries (receive gaps up to 270 ms) occurred before the 15 Hz cadence change, and the firmware watchdog caught each one; the latest build has only been checked with alerts off and the glasses disconnected.

Recording is off by default (`--record` turns it on). Recordings can show bystanders' faces and locations, so get consent, keep them out of git and delete them when done.

The development log, decisions and the evidence behind each claim are in [`docs/project-context.md`](docs/project-context.md).

## Team

Built at HackUTD 2026 by [@chxrro](https://github.com/chxrro), [@NathanS11](https://github.com/NathanS11), [@wxu012](https://github.com/wxu012) and [@minjuna77](https://github.com/minjuna77).
