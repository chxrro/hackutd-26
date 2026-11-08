# Wire protocols

This document is the contract between the three components. If you change a
message, bump its version byte and update all three implementations and the
golden vectors below (each component has a test that checks them).

```text
 meta/ (iPhone app)          hub/ (laptop, Python)              esp32/ (belt)
 glasses → JPEG frames ──WS──▶ perception → haptic policy ──BLE──▶ motors
                     ◀──WS── status JSON
```

All multi-byte integers are **little-endian**.

## 1. Phone → hub: camera frames (WebSocket, binary)

The hub listens on `ws://<laptop-ip>:8765/` (port configurable). The phone
connects as a client. Each **binary** message is one frame:

| Offset | Size | Field        | Notes                                     |
|-------:|-----:|--------------|-------------------------------------------|
| 0      | 2    | magic        | ASCII `HB` (`0x48 0x42`)                  |
| 2      | 1    | version      | `1`                                       |
| 3      | 1    | flags        | reserved, send `0`                        |
| 4      | 4    | seq          | `u32`, increments per sent frame, wraps   |
| 8      | 8    | capture_ms   | `u64`, phone wall clock, Unix ms          |
| 16     | 2    | width        | `u16`, pixels                             |
| 18     | 2    | height       | `u16`, pixels                             |
| 20     | n    | jpeg         | JPEG bytes, rest of the message           |

`capture_ms` comes from the phone's clock and is only informational (latency
logging). The hub measures staleness with **its own receive time**, because the
two clocks are not synchronized.

Golden vector (seq=1, capture_ms=1700000000000, 504×896, jpeg=`ff d8 ff d9`):

```text
48 42 01 00 01 00 00 00 00 68 e5 cf 8b 01 00 00 f8 01 80 03 ff d8 ff d9
```

### Phone → hub text messages (JSON)

```json
{"type": "hello", "client": "ios", "version": 1}
{"type": "phone_status", "stream": "streaming"}
```

`stream` is the glasses stream state as the app sees it (`stopped`,
`waitingForDevice`, `starting`, `streaming`, `paused`, `stopping`).

## 2. Hub → phone: status (WebSocket, text JSON, ~5 Hz)

```json
{
  "type": "status",
  "mode": "levels",
  "healthy": true,
  "reason": null,
  "levels": [0, 0, 140, 0, 0],
  "belt": "connected",
  "inference_ms": 85.2,
  "frame_age_ms": 40.1
}
```

- `mode`: `off` | `levels` | `fault`, the same value the belt was sent.
- `healthy: false` means the hub can't vouch for the output. `reason` says why,
  e.g. `"no camera frames for 1.2 s"`.
- `belt`: `connected` | `connecting` | `disconnected` | `simulated`.

### Control (WebSocket text JSON, additive, draft 2026-11-07)

Lets a paired phone app change hub settings. The frame format, the belt
command, and the existing `hello` / `phone_status` / `status` fields are
unchanged. A client that never sends control messages works as before, and
clients must ignore status fields they don't know.

**Pairing.** At startup the hub generates a random 16-character lowercase hex
**control code**. It prints the code in its terminal and shows it on the
localhost-only dashboard. A client that wants control includes it in `hello`:

```json
{"type": "hello", "client": "ios", "version": 1, "control_code": "3f9c0a1b2d4e5f60"}
```

Without the matching code, frames are accepted as before, but every `control`
message is answered `ok: false, error: "not paired"`. The hub never logs the
code a client sends.

Pairing belongs to one WebSocket connection. After a reconnect, send `hello`
with the code again. A later `hello` with a wrong or missing code unpairs that
connection. The code changes every time the hub restarts.

**Request** (client → hub):

```json
{"type": "control", "id": 12, "action": "set", "settings": {"alert_range": "far"}}
```

| `action`    | Extra fields | Effect |
|-------------|--------------|--------|
| `get`       | none | No effect. Replies with the current settings, so it also checks pairing. |
| `pause`     | none | Obstacle alerts stop. The belt plays the **pause cue**, then a **paused reminder** pulse every 15 s (see "Belt cues" in section 3). Status shows `healthy: false`, `reason: "alerts paused"`, `paused: true`. |
| `resume`    | none | The belt plays the **resume cue** (≤ 0.6 s, status `reason: "resuming alerts"`), then back to normal. |
| `belt_test` | none | The hub plays a short sweep across the motors (≤ 3 s), then goes back to normal. Refused while paused, while a test is running, or while the belt is disconnected. |
| `set`       | `settings` object, one or more keys below | Change settings. Unknown keys reject the whole request. |

`pause` and `resume` are idempotent. `pause` also stops a running belt test.

While paused, a real fault takes priority on the belt and in `reason`. The belt
plays the fault pattern, and `"no camera frames for 1.2 s"`,
`"perception failing: ..."`, `"belt disconnected"` or `"belt connecting"`
replace `"alerts paused"`. When the fault clears and alerts are still paused,
the pause cue plays again. Read `paused` to tell whether alerts are paused.
Don't infer it from `reason`. (Changed 2026-11-08: before this, the belt stayed
`off` during a fault while paused.)

While the pause cue, a paused reminder or the resume cue plays, status shows
`mode: "levels"` with those levels. They are not obstacles. `reason` is
`"alerts paused"` or `"resuming alerts"`. If a fault starts during a cue, the
fault wins. A resume cue cut off by a fault plays once the fault clears.

During a belt test, status shows `mode: "levels"`, `healthy: false` and
`reason: "belt test"`. Those vibrations are not obstacles.

| Setting       | Values | Meaning |
|---------------|--------|---------|
| `classifier`  | `"local"` \| `"nim"` | Person/object labels from the detector, or from the cloud model (if the hub was started with a cloud endpoint configured). |
| `alert_range` | `"near"` \| `"normal"` \| `"far"` | Preset for how early alerts start (distance and time-to-collision thresholds). |
| `record`      | `true` \| `false` | Opt-in session recording, if the hub supports it. The phone must get explicit consent before sending `true`. A hub without recording answers `true` with `error: "recording not available"` and accepts `false`. |

The hub chooses the `alert_range` thresholds and lists them in `hub/README.md`.
`normal` is the default. None of them are validated.

**Reply** (hub → that client only), for every request:

```json
{"type": "ack", "id": 12, "ok": true, "error": null, "settings": {"classifier": "local", "alert_range": "far", "record": false, "paused": false}}
```

`settings` is always the hub's full current state after the request. `ok: false`
means nothing changed; `error` says why (`"not paired"`, `"unknown setting: volume"`,
`"cloud classifier not configured"`, `"refused while paused"`, ...). `id` is
echoed as sent (`null` if missing). Show `error` to the user, but don't parse
it, except to compare it exactly with `"not paired"`.

**Status additions** (hub → all clients, optional fields):

```json
{"paused": false, "settings": {"classifier": "local", "alert_range": "normal", "record": false, "paused": false},
 "sectors": [{"kind": null}, {"kind": "object"}, {"kind": "person"}, {"kind": null}, {"kind": null}]}
```

`sectors[i].kind` is the kind of the largest labelled detection box in that
motor's direction (`"person"`, `"object"` or `null`), for speech and display
only. Box size stands in for "nearest", because no distance is measured. It says
nothing about distance or safety. All kinds are `null` while the hub's input or
perception is faulted, so a stale result is never announced as current. `null`
does not mean "nothing there". Pausing is explicit and announced; it is never a
silent clear-path signal.

## 3. Hub → belt: haptic command (BLE GATT write)

| Item                  | UUID                                   |
|-----------------------|----------------------------------------|
| Service               | `88368a33-70aa-41e2-b071-4f618af1856a` |
| Command (write / write-no-response) | `71dbf1b6-d7d2-4208-954b-b8a38e54618f` |
| Info (read)           | `986b42ae-86e7-4eb6-a579-cd8360e40c0b` |

Advertised name: `HapticBelt`.

Command payload:

| Offset | Size | Field   | Notes                                           |
|-------:|-----:|---------|-------------------------------------------------|
| 0      | 1    | version | `1`                                             |
| 1      | 1    | mode    | `0` off, `1` levels, `2` fault                  |
| 2      | 1    | seq     | `u8`, wraps; for debugging                      |
| 3      | 2    | ttl_ms  | `u16`; firmware clamps to 50–1000 ms            |
| 5      | 1    | count   | number of levels that follow, 1–8               |
| 6      | n    | levels  | `u8` per motor, 0–255, index 0 = wearer's far left |

Golden vector (levels, seq=7, ttl=250, levels `[0, 64, 128, 192, 255]`):

```text
01 01 07 fa 00 05 00 40 80 c0 ff
```

Info payload (read): `[protocol_version, motor_count]`, e.g. `01 05`.

### Meaning of the haptics

- **A motor's vibration = an obstacle in that direction**, relative to where
  the glasses camera is pointing (head direction, not torso). Stronger means
  closer or a sooner estimated collision. It does **not** mean "move this way."
- **Fault pattern** (double pulse on all motors every 2 s) = the system can't
  vouch for its output: no camera frames, perception failing, or the hub link
  lost. The wearer must not treat this, or silence, as a clear path.
- **Silence means no alert, not a clear path.**

### Belt cues (hub, added 2026-11-08)

The hub builds these from ordinary `levels` commands at its normal TTL and send
rate. The firmware and the command format are unchanged. Every cue starts with
a 0.15 s gap and never drives every motor at once, so it can't be mistaken for
the fault pattern. Times are for 5 motors (`L2 L1 C R1 R2`). Level 200, except
the reminder. None of this has been felt on a real belt yet.

| Cue | Pattern | Meaning |
|-----|---------|---------|
| Pause | Outside in: both outer motors, then both inner motors, then the centre, 0.15 s each (0.6 s with the gap). Plays once. | Alerts are paused. Silence that follows is the pause, not a clear path. It also plays when a fault or belt disconnect ends while still paused. |
| Paused reminder | One 0.15 s pulse on both outer motors at low level (110), every 15 s after the pause cue. Configurable (`--pause-reminder`, `0` = off, else ≥ 2 s). | Alerts are still paused. |
| Resume | Inside out: the centre, then both inner motors, then both outer motors, 0.15 s each (0.6 s with the gap). Obstacle alerts start right after. | Alerts are live again. |
| Belt test (existing) | One motor at a time, left to right, 0.4 s each (≤ 3 s). | Test only. Real alerts are replaced for its duration. |

### Belt failsafe behavior (firmware)

1. Every command is valid for `ttl_ms`. The hub re-sends at ~15 Hz.
2. If no valid command arrives before the TTL expires, or BLE disconnects,
   motors stop immediately and the fault pattern plays for up to 30 s, then
   the belt goes silent to save battery.
3. Before the first command after boot, the belt is silent (after a short
   power-on self-test sweep).
