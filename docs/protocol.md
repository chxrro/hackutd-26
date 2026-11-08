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
{"type": "phone_status", "stream": "streaming", "frame_source": "mock"}
```

`stream` is the glasses stream state as the app sees it (`stopped`,
`waitingForDevice`, `starting`, `streaming`, `paused`, `stopping`).

`stream: "starting"` (from any connection, paired or not) also ends a wearer's
stop (`end_session`, see "Control"), as does a new frame. Added 2026-11-08; no
other `stream` value changes anything on the hub. `phone_status` is a report,
not a command: it can't stop the hub.

`frame_source` (optional, additive 2026-11-08) says where the frames on this
connection come from:

| Value          | Meaning |
|----------------|---------|
| `glasses`      | The real glasses camera. |
| `mock`         | Simulated glasses on the phone (Debug builds, Meta MockDeviceKit). Not the real glasses. |
| `test_pattern` | A generated test picture. Not a camera. |

The phone sends it on every `phone_status` (a run starts with `starting`, so
the hub knows the source before the first frame) and re-sends the last
`phone_status` after a reconnect, before any frames. It is not on `hello`:
`hello` goes out at connect time, before a run has picked a source.

The hub treats a missing field, an unrecognized value, or a connection that
hasn't sent `phone_status` yet as `"unknown"`, **never** as `"glasses"`. Each
`phone_status` replaces the previous declaration for that connection, so a
`phone_status` without the field resets it to `"unknown"`. Frames are
attributed to the source their connection last declared. Only `glasses` may be
presented as real glasses input; the hub labels everything else as simulated
or unknown in its status, logs, dashboard and recordings. The field doesn't
change the belt output.

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
  "frame_age_ms": 40.1,
  "frame_source": "glasses"
}
```

- `mode`: `off` | `levels` | `fault`, the same value the belt was sent.
- `healthy: false` means the hub can't vouch for the output. `reason` says why,
  e.g. `"no camera frames for 1.2 s"`.
- `belt`: `connected` | `connecting` | `disconnected` | `simulated`.
- `frame_source` (optional, additive 2026-11-08): only when the hub's source
  is the phone. The `frame_source` declared by the connection that sent the
  newest frame (or the newest `phone_status`, before any frame):
  `glasses` | `mock` | `test_pattern` | `unknown`. Treat anything other than
  `glasses` as not the real glasses. Absent with other hub sources (webcam,
  video, replay); `source` names those.
- Belt reasons (changed 2026-11-08, hub only): `"belt disconnected"` and
  `"belt connecting"` now set `healthy: false` whether or not alerts are paused
  (before, an unpaused hub said `healthy: true` with `belt: "disconnected"`).
  `"belt not acknowledging"` (new) means the BLE link is up (`belt: "connected"`)
  but the belt hasn't acknowledged a command for longer than the command TTL, so
  it has stopped following the hub: it may be playing the fault pattern, or
  nothing. The hub reconnects on its own. See "Write acknowledgement" in section 3.
- Belt status reasons (added 2026-11-08, hub + firmware): with firmware that has
  the status characteristic (section 3, "Status"), `"belt TTL expired"`,
  `"belt playing fault pattern"`, `"belt not applying commands"` and
  `"belt status stale"` also set `healthy: false` while `belt: "connected"`.
- Reason precedence: camera/perception faults (ignored while stopped by the
  wearer), then `"belt disconnected"` / `"belt connecting"`, then
  `"belt not acknowledging"`, then the belt status reasons above, then
  `"stopped by wearer"`, then `"alerts paused"` / `"resuming alerts"`. A belt
  problem also ends a running belt test.
- `"stopped by wearer"` (added 2026-11-08): the paired phone sent `end_session`
  (see "Control"). `healthy: false`; the belt plays the session-ended cue, then
  `mode: "off"`. It is a deliberate stop, never a clear path.
- `belt_firmware` (optional, added 2026-11-08): what the belt itself last
  reported, or `null` when it doesn't report (old firmware, simulated belt, not
  connected). For display and logs; `healthy` and `reason` already account for it.

  ```json
  "belt_firmware": {"build": "3268a632", "mode": "levels", "seq": 17, "status_age_ms": 40,
                    "uptime_ms": 123456, "max_duty": 255, "levels": [0, 0, 140, 0, 0]}
  ```

  `build` is the info characteristic's text (or the status `build_id` in hex),
  `null` if unknown. `mode` and `levels` are the status fields above.
  `status_age_ms` is how long ago the hub received it.
- `belt_confirmed` (optional, added 2026-11-08): `"status"` (the belt's status
  shows it applied a recent command), `"ack"` (writes are acknowledged, but no
  matching status: old firmware, or the status isn't confirming), or `null`
  (nothing confirms the belt is following the hub).

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
| `end_session` | none | Added 2026-11-08. The wearer pressed Stop. The belt plays the **session-ended cue** once, then stays quiet. Status: `healthy: false`, `reason: "stopped by wearer"`, `stopped: true`. See "Deliberate stop" below. |
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

**Deliberate stop vs. lost phone** (`end_session`, additive, 2026-11-08). Without
it, a hub can't tell a wearer who pressed Stop from a phone that crashed, lost
Wi-Fi or was backgrounded: frames just stop, and the belt plays the fault
pattern for as long as the hub runs.

- **The phone** sends `end_session` from Stop *before* it stops the camera, and
  sends no frames after it until its next run begins with `phone_status`
  `"starting"`. It never sends `end_session` when the app is backgrounded or
  terminated, or when the stream fails by itself: those must keep the fault
  pattern.
- **Only a paired connection** can stop the hub. Unpaired, the request is answered
  `"not paired"` and nothing changes, so frames stopping still means the fault
  pattern. `phone_status` can't stop the hub.
- **The hub**, once stopped: plays the session-ended cue once, then sends `off` at
  its normal rate (the belt's TTL failsafe still applies if the hub dies). It
  ignores "no camera frames" and other input/perception faults, also after the
  phone disconnects. Belt problems still show in `reason` and play as before.
  `end_session` clears `paused`, cancels a belt test, and is idempotent (a repeat
  doesn't replay the cue). The cue replays when a belt problem clears while still
  stopped, like the pause cue.
- **A new session** starts when a frame arrives after `end_session`, or when any
  connection sends `phone_status` `"starting"`. Starting is safe from anyone: it
  only brings back normal behaviour, including the fault pattern if no frames
  follow. The **resume cue** plays once the hub is healthy again (the same "alerts
  are live" signal as `resume`), and status returns to normal.
- The stop lives in the hub's memory: a restarted hub is not stopped (it plays
  the fault pattern until frames arrive).

Status addition: `"stopped": true | false` (optional; older hubs omit it, and
answer `end_session` with `"unknown action"`). While stopped, all
`sectors[i].kind` are `null`.

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
| Status (read / notify, added 2026-11-08) | `ba2928d9-3014-49de-8949-bd9933b65d7a` |

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
Since 2026-11-08 (additive) the firmware appends its **build id** as ASCII, at
most 16 bytes: the first 8 hex digits of its git commit, `+` if it was built
with uncommitted changes in `esp32/`, or `unknown` if built without git. Readers
must take the first two bytes as before and treat the rest as optional text.

```text
01 05 33 32 36 38 61 36 33 32        = v1, 5 motors, build "3268a632"
```

### Status (belt → hub, read + notify, added 2026-11-08)

Additive: the command and info formats are unchanged, and a hub that never reads
this characteristic works as before. The firmware **notifies on every mode
change** (in the same 5 ms loop iteration) and otherwise **at least every
500 ms** (the current firmware: every 250 ms). A read returns the most recently
notified value. Fixed binary, little-endian:

| Offset | Size | Field          | Notes |
|-------:|-----:|----------------|-------|
| 0      | 1    | version        | `1` |
| 1      | 1    | mode           | see below |
| 2      | 1    | seq            | `seq` of the last command the firmware **applied** (`0` if none since boot) |
| 3      | 2    | command_age_ms | `u16`, ms since that command was applied; `65535` = none, or ≥ 65.535 s |
| 5      | 4    | uptime_ms      | `u32`, belt `millis()`, wraps after ~49.7 days |
| 9      | 4    | build_id       | `u32`, the first 8 hex digits of the firmware's git commit as a number (`0` = unknown). The info characteristic has the text form. |
| 13     | 1    | max_duty       | `kMaxDuty`, the firmware's PWM cap (0–255) |
| 14     | 1    | count          | number of levels that follow (the belt's motor count) |
| 15     | n    | levels         | `u8` per motor: what the firmware is driving right now (0–255, index 0 = far left). PWM duty is `min(level, max_duty)`. During the fault pattern this is the pattern's instantaneous level. |

`mode`:

| Value | Name           | The belt is ... |
|------:|----------------|-----------------|
| 0     | `idle`         | silent: no command since boot |
| 1     | `off`          | following an `off` command |
| 2     | `levels`       | following a `levels` command (active) |
| 3     | `fault`        | following a `fault` command: fault pattern |
| 4     | `expired`      | no valid command within the TTL: fault pattern |
| 5     | `disconnected` | BLE dropped while active: fault pattern |
| 6     | `dormant`      | the fault pattern ran its 30 s and stopped: silent |

With 5 motors the payload is 20 bytes, which fits one notification at the
default BLE ATT MTU (23). More than 5 motors needs a larger negotiated MTU, or a
notification is truncated. The bench console (`pio device monitor`) only runs
while no hub is connected; its commands show up here with `seq` `0`.

Golden vector (`levels`, seq=7, command_age_ms=40, uptime_ms=123456,
build_id=`0x1a2b3c4d`, max_duty=255, levels `[0, 64, 128, 192, 255]`):

```text
01 02 07 28 00 40 e2 01 00 4d 3c 2b 1a ff 05 00 40 80 c0 ff
```

**How the hub uses it** (hub, 2026-11-08). The hub subscribes when the
characteristic exists and otherwise falls back to the write acknowledgement alone
(older firmware). A **matching** status is one whose `seq` is a command the hub
wrote on this connection less than one command TTL + 250 ms before the hub
received the notification, with `command_age_ms` within the TTL. It is stronger evidence than a write
acknowledgement: the firmware's main loop applied the command, not only its BLE
stack. While the link is up, these make the hub status `healthy: false`:

| `reason` | When |
|----------|------|
| `"belt TTL expired"` | The belt reports `expired`, `disconnected` or `dormant`: it isn't following the hub. |
| `"belt playing fault pattern"` | The belt reports `fault`, but the command it names by `seq` wasn't a `fault` command. |
| `"belt not applying commands"` | The belt reports `idle`, or its applied `seq` doesn't match a recent hub command (writes are acknowledged but not applied). |
| `"belt status stale"` | No status notification for over 1 s (twice the promised 500 ms). |

They're checked only after the connection's first matching status, or 1 s after
the hub's first write if none arrives (so the status from before the hub's first
command doesn't count). `"belt not acknowledging"` takes precedence over all of
them. Two matching, non-faulty statuses in a row clear them. While any of them is
set, the hub sends the fault pattern, like for `"belt not acknowledging"`.

None of this has run over a real BLE radio yet; it is covered by the firmware's
native tests and the hub's simulator (which runs the firmware's `belt_core`).

### Write acknowledgement (hub, added 2026-11-08)

The hub writes commands **with response** (ATT Write Request). The firmware has
always accepted both write types, so nothing changed on the belt. The hub keeps
at most one write in flight and always sends the newest command; the 15 Hz loop
never waits on the radio.

- **Not acknowledging.** No acknowledged write for longer than the command's
  `ttl_ms` (clamped to 50–1000 ms like the firmware; 250 ms by default) while the
  link is up: status `healthy: false`, `reason: "belt not acknowledging"`. Past
  that point the firmware's own TTL has expired. The hub also switches to sending
  the fault pattern, so any write that does get through matches the status.
- **Recovery.** Three acknowledged writes in a row clear it.
- **Reconnect.** A write not acknowledged within 1 s, or 15 write errors in a row,
  drops the connection (the firmware sees a disconnect and plays the fault
  pattern) and reconnects.
- **What an acknowledgement proves.** The belt's BLE stack received the bytes.
  It doesn't prove the main loop applied them (the firmware's 8-entry event queue
  drops events when full) or that the motors moved. Firmware with the status characteristic
  (below) reports what it applied.
- **Old firmware.** If the command characteristic doesn't offer write-with-response,
  the hub logs a warning and falls back to write-without-response with no
  acknowledgement check.

None of this has been measured over a real BLE radio yet. Write-with-response
adds one ATT response per command (15/s) and typically one to two connection
intervals of latency per write. The hub never waits for it, so the send rate is
unaffected while the round trip stays under the 66 ms send period. Above that,
intermediate commands are skipped, and only the newest one is sent. Check the
acknowledged-write rate on the bench.

### Meaning of the haptics

- **A motor's vibration = an obstacle in that direction**, relative to where
  the glasses camera is pointing (head direction, not torso). Stronger means
  closer or a sooner estimated collision. It does **not** mean "move this way."
- **Fault pattern** (double pulse on all motors every 2 s) = the system can't
  vouch for its output: no camera frames, perception failing, or the hub link
  lost. The wearer must not treat this, or silence, as a clear path.
- **Silence means no alert, not a clear path.** After the session-ended cue,
  silence means the wearer stopped the system: no alerts at all.

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
| Session ended (added 2026-11-08) | Three slow pulses on the inner pair (`L1`+`R1`), 0.3 s each with 0.2 s gaps, fading: level 200, 155, 110 (1.45 s with the gap). Plays once, then silence. | The wearer pressed Stop (`end_session`). No alerts until the next Start; the silence that follows is the stop, not a clear path. |

### Belt failsafe behavior (firmware)

1. Every command is valid for `ttl_ms`. The hub re-sends at ~15 Hz.
2. If no valid command arrives before the TTL expires, or BLE disconnects,
   motors stop immediately and the fault pattern plays for up to 30 s, then
   the belt goes silent to save battery.
3. Before the first command after boot, the belt is silent (after a short
   power-on self-test sweep).
