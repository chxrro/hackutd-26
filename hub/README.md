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
    replay.py          plays back a --record session (replay:DIR)
  perception/        frame -> risk per sector
    vision.py          fuses depth + tracked objects
    models.py          Depth Anything V2 and YOLO + ByteTrack wrappers
    geometry.py        sectors, percentiles, risk curves (pure numpy)
    ttc.py             time-to-collision from box growth (pure numpy)
    sweep.py           fake obstacle for bench tests
    classify.py        person vs. object label per tracked box (--classifier)
    nim.py             the same labels from a Nemotron vision model (cloud)
    openvocab.py       detector that finds hazards by text prompt (--detector openvocab)
  haptics.py         risk -> motor levels (instant rise, slow fade, deadband)
  safety.py          when to send the fault pattern instead of levels
  cues.py            pause / resume belt cues and the paused reminder (docs/protocol.md "Belt cues")
  control.py         runtime settings for the phone / dashboard: pause, belt test,
                     alert range, classifier; pairing code (docs/protocol.md "Control")
  belt/              where commands go
    ble.py             the ESP32 over BLE (bleak)
    sim.py             simulator
  viz.py             --show debug window (render() also draws the dashboard preview)
  dashboard/         --dashboard: operator page on http://127.0.0.1:8780/ (plain HTML/JS)
  record.py          opt-in frame recording (--record), wraps any source
  latency.py         per-stage hub latency, logged every 5 s
  bench/             `haptic-hub bench`: hardware-day belt checks and JSON report
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
uv run haptic-hub bench --serial /dev/cu.usbserial-10   # hardware-day checks, pass/fail + JSON report
uv run haptic-hub bench --belt sim --yes                 # the same flow against the simulator

# Operator dashboard in a browser on this laptop
uv run haptic-hub run --source webcam --belt sim --dashboard
#   then open  http://127.0.0.1:8780/
```

Options: `--perception depth|detector` runs one model only (faster),
`--detector openvocab` finds hazards by text prompt (see below),
`--outdoor` uses the outdoor depth model, `--device mps|cuda|cpu`,
`--motion flow` adds optical-flow approach/crossing (experimental, below).

macOS asks for Bluetooth permission (for BLE) and camera permission (for
`--source webcam`) for your terminal app the first time.

Live sources (`webcam`, `stream`) reconnect on their own if the feed drops.
While the feed is down the belt plays the fault pattern. Files play at their
own frame rate and loop. With `--belt sim` the terminal shows the belt, e.g.
`belt LEVELS [ ▃█▃ ]`. It logs a change at most every 0.5 s; add
`--sim-log-interval 0` to see every change, e.g. the 0.15 s steps of the pause
and resume cues.

## Bench checks (`haptic-hub bench`)

One command for hardware day: connect (address, RSSI, connect time), motor
map (operator confirms each motor's position), level ramp, TTL failsafe,
fault pattern by command and by dropped link, reconnect, and BLE write timing
(median/p90 time for the write call to return: **not** motor latency). With
`--serial PORT` it also reads the belt's USB serial log, times TTL expiry from
`[belt] state -> link-lost` and flags rejected commands or reboots. The
firmware has no BLE status characteristic, so this is the only automatic way
to see the TTL lapse. `--only motor-map` re-runs one check. `--yes` skips
prompts and reports the checks that need a person as UNCONFIRMED. A summary
table is printed and a JSON report goes to `hub/bench-reports/` (git-ignored).
Exit code 0 PASS, 1 FAIL, 3 INCOMPLETE, 130 aborted. `--belt sim` runs the whole
flow against the real `esp32/lib/belt_core` logic compiled for the host (the
`haptic_hub.sim.firmware` build; falls back to a Python model of the TTL rules,
with a warning, if there's no C++ compiler). Tests: `tests/test_bench.py`. When to run it, pass
criteria and what to do on failure: [docs/hardware-bringup.md](../docs/hardware-bringup.md).

## Dashboard and remote control

`--dashboard` serves one page on `http://127.0.0.1:8780/` (`--dashboard-port`
changes the port). It is bound to this laptop only, never the network. It
shows:

- the live frame, with people in magenta and objects in orange. The picture is
  labelled when it is frozen, or when the detections are not current;
- the five motors, left to right, and the belt mode (`levels`, `off`, `fault`).
  "Simulated belt" means nothing vibrates;
- a health banner: green "Alerts active", amber "Alerts paused", blue "Belt
  test", and red "FAULT: <reason>" when the hub can't vouch for its output.
  Screen readers announce each change;
- the frame source. Anything other than the glasses says so, e.g. "Laptop
  webcam: NOT the glasses";
- inference time, frame age, and the cloud classifier's model, round trip,
  last reply and error;
- the **control code** the phone needs, and the phone's hub address.

Its buttons use the same control layer as the phone: pause and resume, belt
test, classifier, and alert range. Each request gets a reply on the page. The
page never slows the belt heartbeat. The preview is drawn in a worker thread at
about 8 fps, 480 px wide, and only while a page is open. A page that falls
behind is skipped rather than queued. The server answers only `Host:
127.0.0.1` / `localhost` and refuses WebSocket handshakes from other web
pages' origins, so a website you visit can't pause the belt or read the code.

**Pairing.** At startup the hub prints a new 16-character control code
(`control code (...): 3f9c0a1b2d4e5f60`). The phone sends it in `hello` to be
allowed to change settings. Without it, the phone can still stream frames. The
code is printed once and never logged. Even with `-v`, the hub's WebSocket
servers don't log message payloads.

| Control | Effect |
|---|---|
| pause / resume | Pause: the belt plays the pause cue (outside in, 0.6 s), then one low pulse on the outer motors every 15 s (`--pause-reminder SECONDS`, `0` = off). Status: `healthy: false`, `reason: "alerts paused"`. Resume: the resume cue (inside out, 0.6 s, `reason: "resuming alerts"`), then normal alerts. A camera or perception fault still plays the fault pattern while paused and shows as the reason. See `docs/protocol.md`, "Belt cues". |
| belt test | Each motor in turn, left to right, 0.4 s each (2 s for 5 motors, never over 3 s). Refused while paused. Status reason `"belt test"`. Real alerts are replaced by the sweep for those ≤ 3 s, so don't run it on a walk. |
| classifier | `local` ⇄ `nim` at runtime. `nim` is only allowed if the hub started with a cloud endpoint (`--classifier nim`, `--nim-url`, `--nim-env`, or `NIM_BASE_URL` / `NIM_API_KEY` / `NVIDIA_API_KEY`). Each switch to `nim` probes the endpoint again; if that fails, nothing changes. |
| alert range | Presets for where alerts start (below). |
| record | Not wired to the control layer yet: `true` is refused with `"recording not available"`. Start the hub with `--record` instead (see Recording and replay). |

Alert range presets. **Unvalidated**: they only move where the risk ramps
start. `near_m` (0.6 m) and `ttc_near_s` (1 s), where risk is full, don't
change.

| Preset | Depth alerts start below (`far_m`) | Approach alerts start below (`ttc_far_s`) |
|---|---|---|
| `near` | 1.5 m | 2.0 s |
| `normal` (default, = `config.py`) | 2.5 m | 3.0 s |
| `far` | 3.5 m | 4.0 s |

## Recording and replay

Recording is **off** unless you pass `--record`. It works with any source
(glasses, webcam, video, stream, even a replay) and doesn't change how the
source behaves.

```bash
# Record a glasses session (prints "RECORDING ON: ... session-001")
uv run haptic-hub run --record

# Replay it through the same pipeline at its original timing
uv run haptic-hub run --source replay:recordings/session-001 --belt sim --show
```

Each session goes to the next numbered folder, `hub/recordings/session-NNN/`
(git-ignored; `--record-dir` changes the parent folder):

- `000001.jpg`, ... the frames as the hub decoded them, re-encoded as JPEG (quality 90)
- `frames.jsonl` one line per saved frame: `seq` (the sender's), `file`,
  `received_at` (hub `time.monotonic()`, s), `t` (s since recording started),
  `capture_ms` (the sender's clock, **not** synchronized with the hub; `null`
  if the source has none), `source`, `width`, `height`
- `session.json` source, start time, limits, and on exit the frames written /
  dropped and why recording stopped

Limits: recording stops after `--record-minutes` (10) or `--record-max-frames`
(6000) saved frames, whichever comes first. The hub keeps running.
`--record-every K` saves every K-th frame. Frames are written by a background
thread through a 16-frame queue. If the disk falls behind, the hub drops the
*recorded copy* and logs it, so perception and the belt heartbeat never wait
on the disk. Frames from the glasses are decoded and re-encoded, so recorded
JPEGs are not byte-identical to what the phone sent.

Replay (`--source replay:DIR`) plays a session once at the hub receive timing
it was recorded with, then stops sending frames, so the belt goes to the fault
pattern like a dropped camera. The status `source`, logs and the `--show`
banner say `replay:session-NNN`, so a replay can't pass as a live run. Replayed
frames get a fresh hub receive time and no `capture_ms`.

Recordings can show people's faces and places. Get consent, don't commit or
share them, and delete them when done.

## Latency

Every 5 s the hub logs one line of per-stage timings on its own clock
(median/p90 in ms over that window). For example, `--perception vision` on
an M-series MacBook (MPS) playing a 20 fps synthetic video made from one still
photo (not glasses footage, not a validated figure):

```text
[video:bus_approach.mp4] latency ms p50/p90 | 5s: in 18.8 fps, perception 18.8 fps, dropped 0 | queue 0/0 | infer 34/37 | to belt 26/48 | hub total 61/82 | capture->recv (unsynced clocks, info only) 0/1
```

The same model was slower in other runs on the same laptop (inference p90
150–210 ms, perception 6–12 fps with frames dropped), so read the line during
your own run rather than relying on these numbers.

- `in` frames reaching the hub per second; `perception` results per second;
  `dropped` frames replaced in the 1-slot buffer before perception took them.
- `queue` receive → perception start. `infer` the model's own time.
  `to belt` perception done → the first belt command sent after it (the belt
  loop ticks at 15 Hz, so expect about half a tick). It is when the hub hands
  the command to BLE, not when the motor moves. `hub total` receive → that command.
- `capture->recv (unsynced clocks, info only)` appears when the sender stamps
  `capture_ms` (the phone does). The phone's clock isn't synchronized with
  the laptop's, so it includes the clock offset and can even be negative.
  Don't quote it as network latency.

`--show` draws a short version at the bottom of the window. None of this is
sent to the phone: the status message is unchanged (`docs/protocol.md`).

## People vs. objects

Every tracked box gets a **person** or **object** label. The `--show` window
draws people in magenta (`PERSON #3 (local)`) and objects in orange
(`object:chair #5 (local)`). The labels do not change the belt output yet.

- `--classifier local` (default): the detector's own COCO class. YOLO's
  `person` class means person and every other class means object. This adds
  no model and no measurable time.
- `--classifier nim`: a Nemotron vision model labels the same boxes. YOLO stays
  the fast path for collision risk. In the background, at most `max_hz` (2/s)
  and one request at a time, the hub draws numbered boxes on a 512 px copy of
  the frame and asks the model which are people. The answer is cached per track
  ID. A box without a cloud answer yet keeps its local label, marked `(local)`.
  If boxes wait longer than `stale_s` (3 s) for an answer (timeout, no network,
  HTTP 429, an unreadable reply), every box falls back to its local label and
  the window shows `ERR cloud classifier: no labels for ...`. The belt keeps
  its normal collision alerts: labels don't drive it, so a labeling outage
  must not replace real depth and TTC alerts with the fault pattern. If labels
  ever drive the belt, an unavailable classifier has to become a fault.

Objects the detector doesn't know (walls, poles, curbs) produce no box, so they
get no label. They are only covered by the depth risk. `--detector openvocab`
(below) finds some of them.

### Cloud endpoint

Any OpenAI-compatible `/v1/chat/completions` server that accepts `image_url`
works. Settings come from flags or the environment:

| Setting | Flag | Env | Default |
|---|---|---|---|
| base URL | `--nim-url` | `NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` (hosted) |
| model | `--nim-model` | `NIM_MODEL` | hosted: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`; own server: the first model it lists |
| key | - | `NIM_API_KEY`, else `NVIDIA_API_KEY` | none; required for the hosted API |

`--nim-env FILE` loads `KEY=VALUE` lines first. Keep that file outside the repo.

```bash
# Self-hosted (e.g. vLLM on a Brev GPU, see below), endpoint in a private env file
uv run haptic-hub run --source webcam --belt sim --show \
    --classifier nim --nim-env ~/.config/haptic-hub/nim.env

# NVIDIA's hosted API (build.nvidia.com)
export NVIDIA_API_KEY=nvapi-...      # never commit it
uv run haptic-hub run --source webcam --belt sim --show --classifier nim

# Measure round trips to the endpoint with a real photo (runs the detector once)
uv run haptic-hub nim-bench --image photo.jpg -n 20 --nim-env ~/.config/haptic-hub/nim.env
```

At startup the hub calls `GET /models`. A wrong URL, a missing hosted key, or a
model the server doesn't list stops it with a clear message.

### Serving Nemotron on Brev

Following the [model card](https://build.nvidia.com/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning)'s
vLLM instructions. vLLM 0.20.0 is required. Use one GPU: FP8 weights on an
L40S or H100, BF16 on an A100 80GB, NVFP4 on Blackwell.

```bash
docker run -d --restart unless-stopped --gpus all --ipc=host -p 8000:8000 \
  vllm/vllm-openai:v0.20.0 \
  nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-FP8 \
  --served-model-name nemotron-3-nano-omni --trust-remote-code \
  --max-model-len 16384 --max-num-seqs 8 --gpu-memory-utilization 0.85 \
  --limit-mm-per-prompt '{"image": 1, "video": 0, "audio": 0}' \
  --reasoning-parser nemotron_v3 --kv-cache-dtype fp8 --api-key "$VLLM_API_KEY"
```

We ran this on 1× L40S (Crusoe) with the `v0.20.0-cu129` tag. Brev's HTTPS
share link sits behind a browser login, so reach the server through the Brev
CLI tunnel instead and keep it running:
`brev port-forward <instance> -p 8000:8000`, then
`NIM_BASE_URL=http://localhost:8000/v1`. Put
`NIM_BASE_URL`, `NIM_API_KEY` and `NIM_MODEL=nemotron-3-nano-omni` in
`~/.config/haptic-hub/nim.env` (`chmod 600`). Requests turn thinking off
(`chat_template_kwargs.enable_thinking=false`). Otherwise the model writes a
long chain of thought before every answer. Stop the instance (`brev stop <instance>`)
when you're done. It bills while it runs, and storage still bills until you
delete it.

## Hazards by text prompt (`--detector openvocab`)

YOLO11n only knows the 80 COCO classes: no pole, bollard, door, stairs, curb,
trash can or wall. `--detector openvocab` swaps it for
[YOLOE](https://docs.ultralytics.com/models/yoloe/) prompted with a list of
words. It returns the same tracked boxes (ByteTrack IDs), so TTC, sector risk,
the belt and the person/object labels work unchanged. A box's label is the
prompt it matched; `person` (also `pedestrian`, `people`) maps to **person**,
everything else to **object**. `person` is always in the list.

```bash
uv run haptic-hub run --source webcam --belt sim --show --detector openvocab
uv run haptic-hub run --source video:walk.mp4 --belt sim --detector openvocab \
    --prompts "person,pole,bollard,door,stairs,raised curb,trash can"
```

Defaults are in `config.py` (`openvocab_model`, `openvocab_prompts`,
`openvocab_conf` = 0.25). The first run downloads `yoloe-26s-seg.pt` (31 MB)
and the MobileCLIP2 text encoder `mobileclip2_b.ts` (254 MB) into the current
directory (both gitignored). The encoder only runs at startup to turn the
prompts into embeddings (~0.5 s once downloaded). The new `vision` dependency
is `clip` (Ultralytics' fork of OpenAI CLIP, pinned by commit in
`pyproject.toml`), which provides the tokenizer.

**Licences.** YOLOE code and weights are AGPL-3.0, like YOLO11n.
MobileCLIP2 is under Apple's *ML Research Model* terms: non-commercial research
only. That may be fine for this prototype, but check it before any other use.
Setting `openvocab_model = "yolov8s-worldv2.pt"` (YOLO-World, also supported)
uses OpenAI CLIP (MIT) instead, a 338 MB download to `~/.cache/clip`.

### Measured (2026-11-07, replayed stills, not live video)

Apple M4 Max, MPS, other jobs running (load average ~6–7). Frames at
`infer_width` 384, model input at the default 640. Median / p90 ms per frame.
Each image was held for 20+ frames, like a camera, after warm-up.

| What was timed | YOLO11n | YOLOE-26n | **YOLOE-26s** (default) | YOLO-World v2-s |
|---|---|---|---|---|
| detector `track()` alone | 9.2 / 11.3 | 13.7 / 17.8 | 17.7 / 22.2 | 12.4 / 15.1 |
| `--perception detector`, whole step | 18.3 / 25.7 | 26.1 / 34.6 | 28.9 / 34.8 | – |
| `--perception vision` (+ depth) | 40.4 / 45.7 | 45.4 / 53.7 | 56.1 / 64.7 | – |

The whole-step rows include about 7 ms to shrink a ~1 MP still to 384 px (existing
code, same for every detector). At 15 Hz the budget is 67 ms. `vision` +
YOLOE-26s fits at the median but leaves little headroom: p90 is 65 ms and the
max was 93 ms. If the live frame rate drops, try in order:
`openvocab_model = "yoloe-26n-seg.pt"` (−11 ms, but found less below),
`--perception detector`, or the next step, not built yet: keep YOLO11n every
frame and run the prompts as a slower "hazard pass" (e.g. 3–5 Hz).

What each detector reported on 8 hazard photos from Wikimedia Commons (CC0 /
CC BY / CC BY-SA, not committed) plus ultralytics' `bus.jpg` and `zidane.jpg`,
with the default prompts, at the conf each would use in the hub. **One photo per
hazard is an example, not an accuracy figure.**

| Photo | Hazard in view | YOLO11n (conf 0.35) | YOLOE-26s (conf 0.25) |
|---|---|---|---|
| bollards | row of bollards along the sidewalk | cars, fire hydrant; no bollards | 1 of the bollards (0.69), cars |
| curb | raised curb island in front | cars only | `raised curb` (0.61); plain `curb` matched nothing; also an unclear `door` (0.59) |
| glass door | glass wall/doors right in front | people behind the glass | people behind the glass; **glass missed** |
| low wall | mossy log/low wall on the ground, tree | nothing | `tree trunk` (0.33); **log on the ground missed** |
| pole | utility pole | nothing | **pole missed** (also with `utility pole`, `lamp post`) |
| sign | "sidewalk closed" sign on posts across the path | nothing (a 0.21 "bench") | `sign` (0.50, 0.32) |
| stairs | staircase ahead | nothing | `stairs` (0.83) |
| trash can | knocked-over bin on the sidewalk in front | the fallen bin as "bench" (0.43), cars | upright bins far away (0.41); **fallen bin missed** |
| bus / zidane | people | 4 people + bus / 2 people + tie | 4 people / 2 people |

YOLOE-26n missed the stairs and called the sign "bench". YOLO-World v2-s found
4 of the bollards and the far bins, but missed the stairs, the curb and the
sign. Prompt wording matters: `raised curb` found a curb that `curb` didn't.

Still unsolved with any detector: **transparent glass**, **drop-offs and
curbs** at or below the bottom of the frame (also below the depth band), thin
poles, and things lying on the ground. A box is also not a distance: TTC still
comes from box growth, which a static pole or wall barely produces until it's
close.

## How the risk is computed

1. **Depth** (static obstacles): Depth Anything V2 *metric* estimates meters
   per pixel. Each of the 5 vertical strips takes the 5th-percentile depth in
   the middle band of the image (the bottom is floor). Distance maps to risk
   linearly: 0 beyond `far_m` (2.5 m), 1 at `near_m` (0.6 m).
2. **Tracked objects** (things coming at you): YOLO + ByteTrack keep an ID
   per object. TTC ≈ box height / growth rate, fitted over 0.6 s. Risk is 0
   above 3 s and 1 below 1 s, applied to every strip the box covers.
3. **Fusion**: sector risk = max(depth risk, TTC risk), plus flow risk with
   `--motion flow` (below).
4. **Policy**: rising risk applies instantly, falling risk fades, values
   below the deadband are ignored, then mapped to PWM 90–255.
5. **Safety**: no frames for 1 s, no result for 1.5 s, a result computed
   from a frame received more than 1 s ago (slow model), or a model exception
   means the belt gets the **fault pattern**, never silence.

All thresholds are in `haptic_hub/config.py`. None of them have been
validated yet; measure against a tape measure and tune.

## Optical-flow motion (`--motion flow`, experimental, off by default)

`perception/flow.py` wraps the detector perception (`vision` or `detector`;
other modes refuse the flag). Adapted from Nathan's NVIDIA webcam experiment
(`scripts/visual_motion.py`, PR #3). For each tracked box it fits a similarity
transform to sparse Lucas-Kanade flow between the current frame and one
~0.15 s older:

- **Approach**: the fitted scale *s* over *dt* gives TTC ≈ dt / ln(s), median
  over 0.6 s. It goes through the same 1–3 s risk curve as box-growth TTC.
  It is camera-relative, so walking toward a still pole counts.
- **Crossing**: the box's horizontal motion minus the background's motion at
  the same point, so a head turn is not a crossing. If a near box (≥ 25 % of
  the frame height) moving ≥ 0.1 frame widths/s would sweep into the middle
  band (35–65 % of the width) within 1 s, every sector it sweeps gets risk 0.5.
  A vibration still means an obstacle in that direction, not "move".

Flow only **raises** sector risk (max-merge). A box with too little texture, a
new track, or no reference frame of the right age adds nothing. A failure
raises, so the belt plays the fault pattern. Settings are in `FlowConfig`.

Measured on synthetic scenes (`tests/test_flow.py`), not real footage: TTC
within 25 % on a scripted approach; a head pan is not reported as crossing;
LK tracking failed beyond ~25 px of shift per frame pair, which is why the
reference frame is ~0.15 s old. Cost: median ~3 ms/frame with 1 box and
~6 ms with 5 boxes at 384 px on an M4 Max CPU. Real-camera accuracy and
thresholds are not validated; tune on replayed clips
(`--source video:walk.mp4 --belt sim --show --motion flow`).

## Known limitations

- Monocular depth and box-growth TTC are estimates. Not validated.
- The camera is on the head and the belt on the torso, so direction is
  relative to where the wearer is looking.
- Low obstacles, curbs and drop-offs are mostly outside the depth band.
  `--detector openvocab` finds some hazards COCO lacks, but missed glass, a
  pole and a fallen bin in our test photos (see above).
- Webcam/video results are not glasses results; the status says which source
  is active.
- The dashboard and control layer are tested over loopback with fake
  perception and a simulated belt only. They have not been used with the
  phone app, glasses or a real belt yet.
- Person/object labels have not been checked for accuracy on our own footage,
  local or cloud. Cloud labels arrive a round trip late, so a new box shows its
  local label first.

## Simulation

`uv run python -m haptic_hub.sim` drives the whole chain in a simulated world:
rendered glasses frames go over the real WebSocket protocol into this hub, and its
belt commands go into the real belt firmware logic built for the host. It runs
scripted scenarios (obstacle direction, approaching people, head motion, stale
frames, disconnects, crashes, lost belt writes) and checks what the belt does.
Results are simulation only. See [haptic_hub/sim/README.md](haptic_hub/sim/README.md).
