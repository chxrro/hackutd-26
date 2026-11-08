# Hardware bring-up runbook

The hardware-day order, from bare board to a supervised walk. Do the steps in
order: each one assumes the one before it passed **on the same day, with the
same firmware and hub commit**. If a step fails, fix it there. Don't skip ahead.

> **Safety (from [AGENTS.md](../AGENTS.md)).** This is an experimental
> collision-alert prototype, not a navigation or safety system. Bench first:
> steps (a) to (d) happen at a table, with the belt in a hand or worn seated.
> Any walking happens only in step (e), supervised, at walking pace or slower,
> in a cleared space, with a sighted spotter who can stop it. **Silence is not a
> clear path.** Nobody relies on the prototype unsupervised. Recording video
> needs consent from everyone in view.

Status of this document: written before any hardware test. The `haptic-hub
bench` command was tested only in simulation (`hub/tests/test_bench.py`):
against the real `esp32/lib/belt_core` logic compiled for the host (no radio,
motors or serial), a Python model of the same TTL rules, a fake BLE client and
a pseudo-terminal standing in for the serial port. Nothing here has run on the
real belt, phone or glasses yet. Replace this paragraph with results once it
has.

## What to bring

- Belt: ESP32 DevKit + driver + 5 motors, its power source (power bank or
  battery), USB data cable (some cables only charge).
- Mac with this repo, [PlatformIO](https://platformio.org/) and
  [uv](https://docs.astral.sh/uv/). Bluetooth on.
- iPhone with the app installed (see [meta/README.md](../meta/README.md)),
  Ray-Ban Meta glasses, charged.
- Tape and a marker to label motors M0 to M4. M0 is the wearer's far left, M2
  centre and M4 far right. Wire colours are **not** a reliable guide to the order.
- A run sheet (paper or a shared doc) for the "Record" items below. Don't write
  names of bystanders or anything personal in it.

Before starting, check what you're testing:

```bash
git fetch origin && git log -1 --oneline origin/main   # note the commit
git status                                             # clean tree, or note what's local
```

## (a) Firmware flash and serial console smoke test

Details: [esp32/README.md](../esp32/README.md). Pins and UUIDs are in
`esp32/include/config.h`.

```bash
cd esp32
pio test -e native              # logic tests, no board needed
pio device list                 # find the port, e.g. /dev/cu.usbserial-10
pio run -e esp32dev -t upload   # flash (board on USB)
pio device monitor              # 115200 baud
```

The port name changes with the USB adapter and socket. It has been
`/dev/cu.usbserial-10` before. `ls /dev/cu.usbserial-* /dev/cu.wchusbserial*
/dev/cu.SLAB_USBtoUART` lists the usual ones.

In the monitor, after a reset (EN button or replug):

| Step | You should see / feel | Pass |
|---|---|---|
| Boot | `[belt] 5 motors, self-test`, a short buzz on each motor left to right, `[ble] advertising as "HapticBelt"`, `[console] type h ...` | all four, once (no boot loop) |
| `s` | `state=idle hub=not connected motors=5` | exact |
| `m 0` … `m 4` | each motor buzzes for 1 s | **all five**, including `m 4`. Write down where each one is felt. |
| `l 0 0 255 0 0` | centre motor strong for 2 s | felt at the centre |
| `f` | double pulse on all motors every 2 s, for 4 s | felt on all five |

Pass: every row above. If M*i* is felt somewhere other than position *i*, step
(b) prints the fix. You can carry on.

On failure:
- **No port:** try a data cable, another USB socket, or the USB-serial driver
  (CH340 or CP210x, depending on the board).
- **Boot loop or reset when motors start:** the supply is browning out. Power
  the motors separately from the ESP32's 3.3 V rail, with common ground (see
  the wiring section of the esp32 README).
- **Motor never buzzes:** check the solder joint, the driver channel, the motor
  + to supply. Try that motor's GPIO on another channel.
- **Motors run hot:** lower `kMaxDuty` in `config.h`, reflash.
- **`? hub is connected`:** a hub on this Mac (or another) is connected over
  BLE. Stop it.

Record: firmware commit, port name, power source, where each `m i` was felt,
anything odd in the log.

**Exit the monitor (Ctrl-C) before step (b)** if you'll use `--serial`. Two
readers on one port split the lines between them.

## (b) `haptic-hub bench` on the bench belt

Belt powered, on the table or in a hand. USB to the Mac too, if you can, so
the bench can read its serial log.

```bash
cd hub
uv sync                                   # core only; no vision models needed here
uv run haptic-hub bench --belt sim --yes  # ~30 s dry run against belt_core on the Mac; nothing vibrates (exit 3 is expected)
uv run haptic-hub scan                    # the belt shows up, with RSSI
uv run haptic-hub bench --serial /dev/cu.usbserial-10 --note "bench, belt v1, power bank A"
```

The first BLE run makes macOS ask for Bluetooth permission for your terminal
app. Allow it, then run again.

The bench runs these checks in order and asks the operator `y` / `n` / `r`
(repeat) / `q` (quit) after each stimulus. While a question is waiting, it keeps
sending `off`, so the belt stays quiet. Run one check again with `--only`, e.g.
`--only motor-map` or `--only ttl,fault-disconnect` (`connect` always runs).

| Check | What happens | Pass | Decided by |
|---|---|---|---|
| `connect` | scan, connect, read info | found, protocol v1, 5 motors; prints address, RSSI, scan and connect time | automatic |
| `motor-map` | each motor alone for 1 s, M0 → M4 | every motor felt at its expected position | operator |
| `levels` | centre motor at 0, 30, 60 … 255 (1 s each) | stronger at each step, and the lowest felt level ≤ the hub's weakest alert level (90) | operator |
| `ttl` | buzz, then the hub goes silent, at TTL 250 ms (the hub's) and 1000 ms (easy to feel) | the motor stops by itself and the fault double pulse starts. With `--serial`, `state -> link-lost` is logged within the TTL + 250 ms | serial log + operator |
| `write-timing` | 100 silent writes at 15 Hz, without and with response | no failed writes, p90 ≤ 66.7 ms (one heartbeat), no `rejected command` in the log | automatic |
| `fault-command` | `mode=fault` heartbeats for 4.5 s (what the hub sends when it can't vouch for its output) | double pulse on all motors, about every 2 s | operator |
| `fault-disconnect` | buzz, then the hub drops BLE without sending anything | motor stops, fault pattern starts. With `--serial`, the log shows `hub disconnected` and `link-lost` | serial log + operator |
| `reconnect` | scan and connect again | the belt re-advertises and reconnects | automatic |
| `belt-log` | (with `--serial`) the whole log | no rejected commands, no reboot mid-run | automatic |

The ≤ 66.7 ms write rule and the 250 ms TTL tolerance are bench rules chosen
from the hub's 15 Hz heartbeat. They are not validated latency requirements.
**Write timing is how long the BLE write call takes to return on the Mac, not
motor latency.** "Without response" means the write was handed to macOS's
Bluetooth stack (what the hub does). "With response" means the belt's GATT
server acknowledged it. Neither says when the motor moved.

Output: a summary table, a verdict, and a JSON report in `hub/bench-reports/`
(git-ignored). Exit code: `0` PASS, `1` FAIL, `3` INCOMPLETE (nothing failed,
but a check was skipped or not confirmed by a person), `130` aborted. With
`--yes`, nothing is asked, and every check that needs a person's observation is
reported UNCONFIRMED, never PASS. Use `--yes` for scripted runs, not as the
hardware sign-off.

Pass: verdict **PASS** on the real belt, with `--serial`, in one run. Without
`--serial`, the TTL is only judged by feel, which can't tell 250 ms from 400 ms.
Say so in the run sheet.

On failure:
- **`connect` fails, "no HapticBelt found":** belt powered? Still connected to
  another hub (it stops advertising while connected)? Run `haptic-hub scan`.
  On macOS, `--address` takes the CoreBluetooth UUID that `scan` prints, not a
  MAC address.
- **`connect` fails on the motor count or version:** the firmware and hub are
  from different commits. Reflash from the commit you're testing.
- **`motor-map` mismatch:** the report prints the order to put in
  `kMotorPins` in `esp32/include/config.h` (e.g. `{25, 32, 27, 26, 33}`), or
  swap the wires. Then reflash and run `--only motor-map` again. A motor
  felt nowhere is a wiring or driver fault: go back to (a) with `m <i>`.
- **`levels`: weakest level felt is above 90:** raise `HapticsConfig.min_level`
  in `hub/haptic_hub/config.py` to the lowest level you could feel, and note it.
  That's a hub change: put it on its own branch.
- **`ttl` fails:** check the serial log in the report (`belt_log`). No
  `link-lost` at all means the firmware isn't enforcing the TTL. **Stop:** don't
  go on to (c).
- **`write-timing` fails:** move the belt closer (note the RSSI), turn off other
  BLE devices, and run `--only write-timing` again. Failed writes or a reboot
  in `belt-log` point at power.
- **`fault-*` not felt:** check `l 180 180 180 180 180` on the console. Fault
  pulses are 150 ms at level 180, so weak motors may hide them.

Record: the report file name and verdict, RSSI and distance, and any fix you
made (and the re-run that passed). Copy the summary table into the PR or the
project context. Don't commit the report folder.

After the bench ends, the belt plays the fault pattern for 30 s, then goes
quiet. Any lost link does that, including a clean exit. It's by design.

## (c) Hub with a webcam or video and the real belt

Belt worn seated, or in a hand. Hub details: [hub/README.md](../hub/README.md).

```bash
cd hub
# 1. No model: a fake obstacle sweeps left -> right -> left. Checks hub -> belt alone.
uv run haptic-hub run --source webcam --perception sweep --dashboard
# 2. Real perception (first run downloads models)
uv sync --extra vision
uv run haptic-hub run --source webcam --show --dashboard
# 3. A known clip, to compare with the simulated belt
uv run haptic-hub run --source video:walk.mp4 --show
```

Open `http://127.0.0.1:8780/` for the dashboard. macOS asks for camera
permission for the terminal the first time.

Pass:
- Sweep: the belt follows the sweep smoothly in both directions. The dashboard
  shows belt `connected` and the same bars.
- Real perception: an object (a box, a teammate) moved into the left, centre
  and right of the webcam's view drives the matching motor. Two objects at once
  drive two motors. This checks direction only. Distances and thresholds are
  not validated.
- Failures are felt and shown, never silent:
  - kill the hub with Ctrl-C: the belt stops, then plays the fault pattern
    (TTL path);
  - switch the belt off while the hub runs: the dashboard shows
    `belt disconnected`. Switch it on again: the hub reconnects on its own;
  - unplug the webcam, or end a `stream:` source: fault pattern within about
    1 s (`frame_stale_s`);
  - the hub's latency line (every 5 s) stays sensible: no growing `queue`,
    no frames `dropped` for long stretches.

On failure: if the sweep works but perception doesn't, it's perception (try
`--perception detector`, check the latency line). If the sweep is jerky or the
belt drops out, go back to (b) `--only write-timing` at the same distance.

Record: which perception mode and device, the latency line (median/p90), what
was felt for left, centre, right and two objects, and the fault cases above.
Note it's a webcam, not the glasses. Also note any low-light or camera-motion
cases you tried.

## (d) Phone app and glasses to the hub, with `--record`

Follow the **Test-day checklist** in [meta/README.md](../meta/README.md) (build
and install, Developer Mode, the app's steps, the "what you should see" table
and the network fallbacks). Glasses setup background:
[docs/meta-setup.md](meta-setup.md). Run it first with the simulated belt, then
with the real one:

```bash
cd hub
uv run haptic-hub run --source glasses --belt sim --show --dashboard
uv run haptic-hub run --source glasses --show --dashboard --record --record-minutes 3
```

**Recording needs consent.** Only record with the agreement of everyone who
will be in view. Keep it short. Recordings go to `hub/recordings/` (git-ignored).
Don't commit or share them, and delete them when you're done.

Pass: every row in the checklist's "Working" column. Belt motors follow
objects in front of the glasses (seated, head still, then turning the head
slowly). Stopping the camera in the app, turning off the phone's Wi-Fi, or
quitting the app gives the fault pattern on the belt within about 1 s and a
fault reason in the app and on the dashboard.

Replay the recording through the same pipeline with the simulated belt:

```bash
uv run haptic-hub run --source replay:recordings/session-001 --belt sim --show
```

On failure: use the checklist's network section (Safari test, firewall,
hotspot fallbacks). If frames arrive but the belt is quiet, check the
dashboard health banner before blaming perception.

Record: phone and glasses models and firmware, network used (Wi-Fi, hotspot,
USB), frames per second and `hub total` from the latency line, the
`capture->recv` value labelled "unsynced clocks", the session folder name (not
its contents), and each fault case.

## (e) Full loop: bench, then a supervised walk

**Bench loop (seated or standing still).** Belt worn, glasses on, phone app
streaming, hub on the real belt (no `--belt sim`). A teammate moves soft objects
(cardboard boxes) to the wearer's left, centre and right, and approaches slowly.
Then repeat the fault cases from (d) while worn. Pass: the wearer names the
direction correctly each time, and feels every fault case as the fault pattern.

**Supervised walk (only after everything above passed today).**

- A sighted spotter walks beside the wearer, within arm's reach, and can stop
  the test. Agree on a stop word.
- The wearer keeps their usual mobility aid or sight. The belt is an extra
  signal under test, not the thing keeping anyone safe.
- An indoor corridor or room, cleared of real hazards (stairs, drop-offs,
  glass, traffic), with soft obstacles placed by the team. Walking pace or
  slower.
- Stop at once on any doubt: confusing output, a fault pattern, a dropped
  link, a hot motor, a low battery.

Pass: a short walk where every alert is noted, and the spotter never has to
step in because of the belt. This doesn't validate the system. It's one
supervised observation.

Record, per walk: date, hub and firmware commits, the step (b) report file,
the route and obstacles, each alert felt vs. what was actually there (hits,
misses, false alerts), every fault pattern and its cause, the latency line, and
battery level before and after. Don't record faces or bystanders without
consent. Update [docs/project-context.md](project-context.md) with what was
verified and what still isn't.

## Gaps found while building the bench (proposals, not changes)

None of these are implemented. Each one is a firmware or protocol change, so
it needs the owner of `esp32/` and an update to [protocol.md](protocol.md)
before anyone builds it.

1. **No way to observe the belt's state over BLE.** The GATT service has a
   write-only command and a read-only info characteristic. TTL expiry, link-lost
   and the fault pattern can't be seen from the hub, so the bench times them
   from the USB serial log instead (`--serial`). That needs a cable, and it
   includes serial latency. *Proposal:* an additive read+notify **status**
   characteristic, e.g. `[version, state (idle/active/link-lost/dormant),
   last_seq, mode, ms_since_last_command u16, rejected_count u8]`, notified on
   every state change. The existing command and info stay as they are. The hub
   could then show "belt in fault" on the dashboard, and the bench could time
   the TTL without USB.
2. **`mode=fault` commands leave no trace in the serial log.** The controller
   stays `active`, so `fault-command` is operator-only. *Proposal:* log mode
   changes (`[belt] mode -> fault`), or include the mode in (1).
3. **Info doesn't identify the firmware build.** The report can't record which
   firmware was tested, and a stale flash looks the same as a current one.
   *Proposal:* add a firmware version or commit string, in a new read
   characteristic or after the existing two info bytes (the hub's readers
   already accept more than 2 bytes; check the phone app if it ever reads info).
4. **`kMaxDuty` isn't visible to the hub.** If it's lowered for hot motors,
   levels above it feel the same, and the `levels` check can't tell why.
   *Proposal:* report it in the info or status data.
5. **Every hub exit, even a clean one, starts 30 s of fault pattern.** By
   design today: the belt can't tell a clean exit from a crash. That's the
   safe default and isn't a bug. If it's confusing on test days, the team could
   decide on an explicit "hub shutting down" signal. That's a safety decision
   for the team, not a bench fix.
6. **Opening the serial port may reset the board.** On many ESP32 dev boards,
   DTR/RTS reset the board. The bench opens the port first, waits 3 s
   (`--serial-settle-s`), and reports `serial_open_reset_board`. Not checked
   on the real board yet.
