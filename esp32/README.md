# esp32/: belt firmware

Receives haptic commands from the hub over Bluetooth LE and drives 5 vibration
motors. The wire format is in [../docs/protocol.md](../docs/protocol.md).

```text
include/config.h       pins, PWM, BLE UUIDs: edit this for your build
src/main.cpp           wiring: BLE events -> controller -> motors, status notifications
src/ble_link.*         NimBLE GATT server (only file that knows about BLE)
src/motors.*           PWM output (only file that touches GPIO)
lib/belt_core/         pure C++ logic, unit-tested on your laptop
  belt_protocol.*        decode hub commands
  haptic_controller.*    TTL expiry, fault pattern, link-lost behavior
  belt_status.*          status report encoding + when to notify
  console.*              serial bench-test commands
scripts/build_id.py    stamps the git commit into the build (info + status)
test/test_belt_core/   host tests for belt_core
```

## Build, flash, test

Needs [PlatformIO](https://platformio.org/) (`brew install platformio`).

```bash
cd esp32
pio test -e native            # logic tests on the laptop, no board needed
pio run -e esp32dev           # build
pio run -e esp32dev -t upload # flash (board on USB)
pio device monitor            # serial log at 115200
```

On boot the belt buzzes each motor once, left to right (self-test), then
advertises as `HapticBelt`. From the hub, run `haptic-hub belt-test` to check
each motor over BLE.

### Serial bench-test console (no hub needed)

In `pio device monitor`, type:

```text
m 2         buzz motor 2 (center) for 1 s     m 0 120   motor 0 at level 120
l 0 0 255 0 0   set all 5 levels for 2 s
f           fault pattern for 4 s             t         self-test sweep
s           state, mode, max duty, build      h         help
```

The console is disabled while the hub is connected over BLE. When a console
test ends the belt goes quiet; it doesn't play the fault pattern, since nothing
failed.

## Wiring (ESP32 DevKit + ULN2803)

```text
ESP32 GPIO25 ──▶ ULN2803 1B      1C ──▶ motor 0 (−)   far left
ESP32 GPIO26 ──▶ ULN2803 2B      2C ──▶ motor 1 (−)
ESP32 GPIO27 ──▶ ULN2803 3B      3C ──▶ motor 2 (−)   center
ESP32 GPIO32 ──▶ ULN2803 4B      4C ──▶ motor 3 (−)
ESP32 GPIO33 ──▶ ULN2803 5B      5C ──▶ motor 4 (−)   far right
ESP32 GND    ──▶ ULN2803 pin 9 (GND) ──▶ battery/power-bank GND
Motor supply + ──▶ every motor (+) and ULN2803 pin 10 (COM, flyback diodes)
```

- Motor index 0 is the wearer's **far left**. The order must match, or alerts
  point the wrong way. Check it with `haptic-hub belt-test`.
- Don't drive motors straight from GPIO pins. They draw far more current than
  a pin can supply.
- Coin ERM motors are usually rated about 3 V. The ULN2803 drops about 1 V, so
  from a 5 V supply the motor sees about 4 V. If your motors run hot, lower
  `kMaxDuty` in `include/config.h` (e.g. to 190).
- A logic-level N-MOSFET per motor (with a flyback diode) works instead of the
  ULN2803.

## Safety behavior (what the wearer feels)

| Situation | Belt |
|---|---|
| Boot, before the hub connects | self-test sweep, then silent |
| Hub sending levels | motors follow the levels |
| Hub says it can't vouch for itself (`fault`) | double pulse on all motors every 2 s |
| No command within the TTL (hub crashed or out of range), or BLE dropped | motors stop, fault pattern for 30 s, then silent |

All of this is implemented in `lib/belt_core/src/haptic_controller.cpp` and
covered by `pio test -e native`.

## What the belt reports back

The BLE server requests a 15–30 ms connection interval, zero peripheral
latency, and a 4 s link supervision timeout. The phone chooses the final
parameters. Serial logs show the negotiated interval when it changes. This
radio scheduling request does not extend the command TTL (250 ms in the
iPhone relay); late commands still trigger the firmware watchdog. The request
follows [Apple's connection-parameter guidance](https://developer.apple.com/library/archive/qa/qa1931/).

- **Status characteristic** (read + notify, docs/protocol.md section 3 "Status"):
  mode (`idle` / `off` / `levels` / `fault` / `expired` / `disconnected` /
  `dormant`), the seq of the last applied command and its age, the motor
  levels, uptime, build id and `kMaxDuty`. It notifies on every mode change and
  every 250 ms. The hub uses it to tell "the BLE stack got the bytes" apart
  from "the firmware is following them".
- **Info characteristic**: `[protocol_version, motor_count]` followed by the
  build id as text, e.g. `3268a632` (`+` = built with uncommitted changes in
  `esp32/`, `unknown` = built without git).
- **Serial log**: `[belt] build ...` at boot, `[belt] state -> ...` on controller
  state changes, and `[belt] mode -> ... (seq N)` on every status mode change,
  so a hub `fault` command (state stays `active`) shows up as `mode -> fault`.
- **USB timing diagnostics**: `[ble-diag]` every five seconds while connected
  reports payload count, maximum callback-to-callback receive gap, maximum
  callback-to-main-loop queue delay, dropped event count, and the latest BLE
  RSSI when available. Maxima and counts reset on connection. These distinguish
  delayed delivery from firmware processing stalls; they do not measure the
  phone's send time or motor motion. No payloads or peer identities are logged.

The build id is generated by `scripts/build_id.py` into
`.pio/build/<env>/generated/belt_build_id.h` (not committed). Real-board USB
tests on 2026-11-08 confirmed status reporting and repeated command expiry;
reliable phone delivery remains unresolved. See the investigation evidence in
[project context](../docs/project-context.md).
