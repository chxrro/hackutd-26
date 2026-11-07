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

### Belt failsafe behavior (firmware)

1. Every command is valid for `ttl_ms`. The hub re-sends at ~15 Hz.
2. If no valid command arrives before the TTL expires, or BLE disconnects,
   motors stop immediately and the fault pattern plays for up to 30 s, then
   the belt goes silent to save battery.
3. Before the first command after boot, the belt is silent (after a short
   power-on self-test sweep).
