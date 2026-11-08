# hub/: the center piece

Runs on a laptop. Receives glasses frames from the iPhone app, estimates
collision risk in 5 directions, and drives the belt over Bluetooth LE.

```text
haptic_hub/
  protocol.py        wire formats (docs/protocol.md), shared with meta/ and esp32/
  types.py           data passed between stages
  config.py          every tunable threshold, in one place
  app.py             main loop: source -> perception -> policy -> belt
  sources/           where frames come from
    glasses.py         WebSocket server for the phone (default)
    camera.py          webcam, video file, or live network stream (OpenCV/FFmpeg)
  perception/        frame -> risk per sector
    vision.py          fuses depth + tracked objects
    models.py          Depth Anything V2 and YOLO + ByteTrack wrappers
    geometry.py        sectors, percentiles, risk curves (pure numpy)
    ttc.py             time-to-collision from box growth (pure numpy)
    sweep.py           fake obstacle for bench tests
  haptics.py         risk -> motor levels (instant rise, slow fade, deadband)
  safety.py          when to send the fault pattern instead of levels
  belt/              where commands go
    ble.py             the ESP32 over BLE (bleak)
    sim.py             simulator
  viz.py             --show debug window
```

## Setup

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
cd hub
uv sync                    # core only: belt test, sweep, unit tests
uv sync --extra vision     # + torch, transformers, ultralytics (real perception)
uv run pytest              # unit + end-to-end tests (no models, no hardware)
```

The first real run downloads Depth Anything V2 Small (~100 MB) and
`yolo11n.pt` (~6 MB).

## Run

```bash
# The real thing: phone -> hub -> belt
uv run haptic-hub run
#   prints:  in the phone app, set the hub address to  ws://192.168.x.y:8765

# Develop perception with the laptop webcam and a simulated belt
uv run haptic-hub run --source webcam --belt sim --show

# Replay a recorded walk
uv run haptic-hub run --source video:walk.mp4 --belt sim --show

# Any live video feed OpenCV/FFmpeg can open (RTSP, HTTP MJPEG, ...)
uv run haptic-hub run --source stream:rtsp://192.168.1.50:8554/live

# Bench-test phone + belt without any model (fake sweeping obstacle)
uv run haptic-hub run --perception sweep

# Belt only
uv run haptic-hub scan
uv run haptic-hub belt-test
```

Options: `--perception depth|detector` runs one model only (faster),
`--outdoor` uses the outdoor depth model, `--device mps|cuda|cpu`.

macOS asks for Bluetooth permission (for BLE) and camera permission (for
`--source webcam`) for your terminal app the first time.

Live sources (`webcam`, `stream`) reconnect on their own if the feed drops.
While the feed is down the belt plays the fault pattern. Files play at their
own frame rate and loop. With `--belt sim` the terminal shows the belt, e.g.
`belt LEVELS [ ▃█▃ ]`.

## How the risk is computed

1. **Depth** (static obstacles): Depth Anything V2 *metric* estimates meters
   per pixel. Each of the 5 vertical strips takes the 5th-percentile depth in
   the middle band of the image (the bottom is floor). Distance maps to risk
   linearly: 0 beyond `far_m` (2.5 m), 1 at `near_m` (0.6 m).
2. **Tracked objects** (things coming at you): YOLO + ByteTrack keep an ID
   per object. TTC ≈ box height / growth rate, fitted over 0.6 s. Risk is 0
   above 3 s and 1 below 1 s, applied to every strip the box covers.
3. **Fusion**: sector risk = max(depth risk, TTC risk).
4. **Policy**: rising risk applies instantly, falling risk fades, values
   below the deadband are ignored, then mapped to PWM 90–255.
5. **Safety**: no frames for 1 s, no result for 1.5 s, a result computed
   from a frame received more than 1 s ago (slow model), or a model exception
   means the belt gets the **fault pattern**, never silence.

All thresholds are in `haptic_hub/config.py`. None of them have been
validated yet; measure against a tape measure and tune.

## Known limitations

- Monocular depth and box-growth TTC are estimates. Not validated.
- The camera is on the head and the belt on the torso, so direction is
  relative to where the wearer is looking.
- Low obstacles, curbs and drop-offs are mostly outside the depth band.
- Webcam/video results are not glasses results; the status says which source
  is active.
