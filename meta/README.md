# meta/: iPhone app for the Meta glasses

## Test-day checklist (glasses → phone → hub)

> Experimental prototype. Test seated or standing still, with a sighted
> supervisor. **Silence is not a clear path.** Don't walk with it.

**1. Build and install** (Mac, iPhone plugged in, unlocked, "Trust" tapped,
iOS Developer Mode on: Settings → Privacy & Security → Developer Mode)

```bash
cd meta
cp Config/Local.xcconfig.example Config/Local.xcconfig   # once: DEVELOPMENT_TEAM, BUNDLE_ID_SUFFIX
xcodegen generate
open HapticBelt.xcodeproj        # pick the iPhone as destination, press Run (easiest)
```

Or from the terminal (`<UDID>` from `xcrun devicectl list devices`, "Reality: physical"):

```bash
xcodebuild -project HapticBelt.xcodeproj -scheme HapticBelt -configuration Debug \
  -destination 'platform=iOS,id=<UDID>' -derivedDataPath build -allowProvisioningUpdates build
xcrun devicectl device install app --device <UDID> build/Build/Products/Debug-iphoneos/HapticBelt.app
xcrun devicectl device process launch --console --device <UDID> com.hackutd26.hapticbelt<BUNDLE_ID_SUFFIX>
```

`-allowProvisioningUpdates` (or Xcode's Run) registers a new iPhone with the
team the first time. The development profile covers only registered devices.

**2. Hub** (Mac):

```bash
cd hub && uv sync --extra vision
uv run haptic-hub run --source glasses --belt sim --show
#   prints:  in the phone app, set the hub address to  ws://<mac-ip>:8765
```

The address it prints is a guess (the default route). If the Mac has several
networks, check with `ipconfig getifaddr en0` (Wi-Fi).

**3. Glasses** (once per phone): Meta AI app has the glasses paired and
**Developer Mode** on (Meta AI → Settings → App Info → tap the version 5×).
Developer Mode switches off after glasses firmware updates.

**4. App**, top to bottom:
1. **Connect glasses in Meta AI** → bounces to Meta AI → back. If the SDK says
   the glasses' app component needs an update (seen on 2026-11-07 with
   `ios/`), tap **Update glasses app in Meta AI**, finish it there, come back.
2. **Start camera**. The first time, Meta AI asks for glasses camera
   permission. Camera should go `starting` → `streaming` and show a preview.
3. Hub address = what the hub printed. Turn on **Connect to hub**. Allow
   **Local Network** when iOS asks.
4. Keep the app in the foreground, screen on.

**5. What you should see**

| Where | Working | Not working |
|---|---|---|
| App, Hub → Link | `connected`, **Frames sent** climbing | `connecting…` / `retrying (…)`, see network below |
| Hub log | `phone connected from ('<phone-ip>', …)`, `phone says hello`, `phone glasses stream: streaming`, `health: ok` | nothing (phone never reached the Mac), or `health: no camera frames for N s` (connected, no frames) |
| App, Hub prediction | **levels**: green "Hub input healthy", red bars of varying height | **fault**: orange "Not reliable: <reason>", flat orange bars. "No status from the hub" = no status for 2 s: treat it as a fault |
| Hub terminal (`--belt sim`) | `belt LEVELS [ ▃█▃ ]` | `belt FAULT  [!!!!!]` |

Fault at startup (`perception warming up`, `waiting for camera frames`) is
expected for the first seconds.

**6. Network: if the phone can't reach the hub**

First, find which part fails. In Safari on the iPhone, open
`http://<mac-ip>:8765/`:
- Page says **"Failed to open a WebSocket connection…"**: the network path and
  hub are fine. The app is the problem: check the address, and check
  Settings → Privacy & Security → **Local Network** → Haptic Belt is on.
- Page **times out**: packets don't reach the Mac (client isolation, wrong IP,
  or firewall). Try the fallbacks below.
- **"Could not connect to the server"**: the Mac is reachable but nothing is
  listening. The hub isn't running, or you used the wrong port.

App link errors mean the same: `retrying (The request timed out.)` means
unreachable, `retrying (Could not connect to the server.)` means nothing is
listening, and an "offline" or "local network" error usually means the Local
Network permission is off.

Fallbacks, in order:
1. **Same Wi-Fi, firewall open.** System Settings → Network → Firewall: off,
   or allow incoming connections for Python. Check with
   `/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate`. Venue and
   campus Wi-Fi often isolate clients; if the Safari test times out with the
   firewall off, move on.
2. **iPhone Personal Hotspot, Mac joins it.** iPhone: Settings → Personal
   Hotspot → Allow Others to Join. Mac joins that Wi-Fi. Restart the hub so it
   prints the new `172.20.10.x` address and use that. Needs a carrier plan with
   hotspot.
3. **Same hotspot over USB.** With Personal Hotspot on and the iPhone plugged
   in, the Mac gets an "iPhone USB" network (System Settings → Network). Turn
   Mac Wi-Fi off so the hub prints the `172.20.10.x` address. No Wi-Fi needed.
4. **Another device's hotspot or a travel router** that both the iPhone and Mac
   join (a teammate's phone, a pocket router). Run the same Safari test.

Not tested yet: whether the glasses camera stream still works while the iPhone
is a hotspot (fallbacks 2 and 3). The SDK may use the phone's Wi-Fi for the
glasses link. Check that the camera still shows `streaming`.

## Overview

Streams the Ray-Ban Meta camera to the hub over Wi-Fi and shows what the belt
is doing. It doesn't run the model and doesn't talk to the belt; the hub does
both.

```text
HapticBelt/
  App/          HapticBeltApp (entry, SDK configure), AppModel (wires the parts)
  Glasses/      GlassesController: the only code that uses the Meta SDK
  Forwarding/   FrameForwarder: newest-frame-wins, JPEG encode, send
  Hub/          HubClient (WebSocket, auto-reconnect), FramePacket, HubMessages, HubAddress
  UI/           ContentView: glasses / hub / belt sections
  Resources/    Info.plist (MWDAT keys), entitlements
HapticBeltTests/ protocol golden-vector tests (no SDK, no device needed)
project.yml      XcodeGen spec (the .xcodeproj is generated, not committed)
Config/          Base.xcconfig + your gitignored Local.xcconfig
```

## Requirements

- iPhone on iOS 17.2+, Xcode 26.4+ (Meta's minimums), an Apple ID for signing
- Ray-Ban Meta glasses paired in the **Meta AI** app, with **Developer Mode** on:
  Meta AI → Settings → App Info → tap the version 5 times → Developer Mode.
  It switches off after firmware updates; turn it back on.
- `brew install xcodegen`

No Meta developer account is needed for this. With Developer Mode, `MetaAppID`
is `0` (set in `Config/Base.xcconfig`). Using the SDK means accepting Meta's
[Wearables Developer Terms](https://wearables.developer.meta.com/terms) and
[Acceptable Use Policy](https://wearables.developer.meta.com/acceptable-use-policy).
SDK analytics are opted out in `Info.plist`.

## Build and run

```bash
cd meta
cp Config/Local.xcconfig.example Config/Local.xcconfig   # set DEVELOPMENT_TEAM + BUNDLE_ID_SUFFIX
xcodegen generate
open HapticBelt.xcodeproj                                 # pick your iPhone, Run
```

Tests (simulator, no glasses):

```bash
xcodebuild test -project HapticBelt.xcodeproj -scheme HapticBelt \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro'
```

If signing fails on the two Wi-Fi entitlements (`HotspotConfiguration`,
`wifi-info`) with a free Apple account, remove them from
`HapticBelt/Resources/HapticBelt.entitlements`. Meta's sample includes them for
the Wi-Fi link to the glasses; streaming may still work over Bluetooth without
them, but that hasn't been tested.

## Using it

1. Start the hub on the laptop (`uv run haptic-hub run` in `hub/`). It prints
   the address to use.
2. In the app: **Connect glasses in Meta AI**. You'll bounce to Meta AI and back.
3. **Start camera**. The first time, Meta AI asks for camera permission.
4. Enter the hub address and turn on **Connect to hub**.
5. The Belt section shows the live motor levels. "Not reliable: …" means the
   belt is playing the fault pattern.

Keep the app in the foreground; the screen stays awake while streaming. iOS
suspends the stream when the app is backgrounded or the phone locks.

## Phone vibration bench test (no belt)

The glasses remain the camera. Hold the iPhone to feel alerts while the laptop
runs the existing baseline, with no ESP32 connection:

```bash
cd hub
uv run haptic-hub run --source glasses --perception vision --belt sim --device mps --show
```

In Haptic Belt, connect the hub and start the camera. Tap **Test phone
vibration** to feel one sample buzz (this turns automatic vibration off), then
enable **Vibrate phone for alerts**. It defaults off each app launch.

- A single 120 ms buzz, at most once every 1.2 seconds, means an obstacle alert.
  Higher urgency uses two 120 ms buzzes (starts at 0 and 200 ms), at most once
  every 0.6 seconds; escalation replaces the previous pattern immediately.
  Urgency starts at motor level 220 and persists until below 200 to avoid
  threshold chatter. These are experimental level thresholds, not distances.
  **Phone strength** scales amplitude from 20–100% (default 70%). The phone has
  one actuator: it does not reproduce the five belt locations. The screen lists
  active obstacle directions relative to the glasses, never travel instructions.
- Three sharp taps, at most once every three seconds, mean input is unreliable.
  Disconnection, stopped camera, hub fault, missing/malformed status, camera age
  above one second, or no fresh status for one second triggers this state.
- Turning the toggle off or pressing **Stop camera** cancels phone haptics.
  Backgrounding/locking stops haptics; fresh status is needed after returning.
  All haptic patterns are finite, not looping. Core Haptics failures are shown.
- Status continues to say `belt: simulated`: this is a phone substitute, not
  belt hardware verification. Silence is not a clear-path indication.

Start stationary with a teammate moving a large obstacle through left, center,
and right. Compare the screen's bars and direction text with the phone pulse.
Turn off **Connect to hub** while vibration is enabled to check the fault taps,
then reconnect to check recovery. Do not use this unvalidated test for walking
navigation. No footage is recorded by the command above.

## Stream settings

`GlassesController` asks for `.raw`, `.medium` (504×896) at 15 fps.
`FrameForwarder` limits sustained output to 15 fps as JPEG (quality 0.6),
allowing a two-frame burst to tolerate camera callback jitter. It drops frames
while one is still in flight and never queues images. Change the stream and
forwarding settings in those two files.

The Hub section includes bench diagnostics: camera callbacks and successful
sends per second, average JPEG encode/packet-build and WebSocket send time in
milliseconds, and cumulative rate-limit versus in-flight drops. The rate and
timing readings cover the latest roughly one-second interval. A send completing
does not measure delivery to the laptop; use the hub's incoming frame log for
that. Failed sends can include attempts made before connecting to the hub.

## Glasses speech and attributed hazards

The app can synthesize short English phrases on the iPhone and play them over
the selected Bluetooth audio output. This does not invoke Meta AI or use a
cloud speech service. It does not request microphone access.

1. Use **Choose audio output** in the app to select the glasses. Bluetooth
   pairing / Meta camera connection is separate from the selected audio route.
   Check **Audio volume** and adjust with the phone/glasses volume controls.
2. With the camera streaming, tap **Test glasses speech**. The explicit test
   phrase is not a hazard warning; this button turns automatic speech off.
3. Check **Audio output** and confirm the phrase is heard through the glasses.
   Playback status shows waiting, speaking, finished, blocked, or canceled.
   Even an iOS completion callback is not confirmation of audible delivery.
4. Turn on **Speak obstacle alerts**. **Include object and motion** switches
   between brief generic direction and attributed object/motion wording.

Automatic speech and phone vibration default off. Both use the same validated
hub snapshot. Ordinary obstacle changes have a three-second minimum spacing;
an urgency escalation can preempt with “Urgent,” with a two-second guard against
repeated escalations. A fault preempts obstacle speech. With **Repeat ongoing
alerts** on (default), a continuing notice repeats every 12 seconds, urgent
alert every 6 seconds, and unavailable state every 15 seconds, always evaluated
from current data. Switch it off for changes-only speech. There is no queue of
old warnings. **Repeat current alert** explicitly repeats the latest assessment;
if no alert is active, it says that this does not mean the path is clear.
Quiet, stale input, disconnection, backgrounding,
manual stop, and audio-route changes cancel obsolete speech. Automatic speech
pauses while VoiceOver runs; the existing health announcements remain available
through VoiceOver. Bluetooth is required so the app does not deliberately fall
back to the phone speaker. Use the glasses/phone volume controls.

**Learn the alert patterns** lets you select obstacle, higher urgency, or
unavailable, then **Play selected test**. Each finite vibration is accompanied
by a spoken explanation beginning “Pattern test.” Practice turns both automatic
output toggles off; re-enable them afterward. A missing Bluetooth route is
reported on screen; the phone buzz can still play. Strength changes apply to
future phone patterns only, not to ESP32 PWM. The real belt's existing patterns
and protocol are unchanged and still need hardware calibration.

The additive `hazards` contract is documented in [protocol.md](../docs/protocol.md).
Old hubs still support generic alerts. Unknown attribution stays generic.
Object names are spoken only from a small local vocabulary and only when linked
to the winning risk; no precise distance or safe-travel instruction is spoken.
Motion-aware crossing phrases require the hub's opt-in `--motion flow`; they are
experimental. A stationary obstacle approached by the wearer can be reported as
getting closer. Directions follow the camera, not the torso.

This build adds the communication/attribution foundation. It does not implement
hand suppression, validated motion classification, or calibrated distance.
Camera-plus-Bluetooth audio coexistence and audible latency require physical
testing, including interruptions and disconnects. Speech does not delay haptics.

Audio-route follow-up: route warnings are derived from current routing rather
than latched after one failed test. The app refreshes the route on foregrounding
and once per second, and cancels speech when the selected output changes. It
avoids resetting the audio category for every phrase. The system picker owns
selection; the app cannot force a merely paired accessory to accept audio.

Speech playback follow-up: a successful audio-session activation clears a stale
interruption flag, so a missing interruption-ended notification cannot silently
block a later explicit test. Requests expose their blocked reason, and synthesizer
callbacks report start/finish/cancellation. A request that does not start within
five seconds is canceled rather than left pending. On the physical iPhone 17,
the output showed **RB Meta 02JV** at 50% volume and the owner confirmed hearing
the test after this fix. Concurrent live camera/audio remains to be tested.

## Connect the ESP32 belt directly to the iPhone

The phone can own the belt's BLE connection while the laptop runs perception.
This uses the existing ESP32 firmware and protocol, with five motors. It is
optional; phone vibration and glasses speech remain available independently.

1. Flash the project's `esp32/` firmware using its README and power the belt.
   Disconnect the laptop's BLE belt client; use only one BLE central.
2. Run the hub with its simulated transport, which still computes real levels:

   ```bash
   cd hub
   uv run haptic-hub run --source glasses --perception vision --belt sim \
     --device mps --classifier local --motion flow --show
   ```

3. Install this iPhone build. Under **ESP32 belt · phone Bluetooth**, tap
   **Find ESP32 belt**, allow Bluetooth if requested, and select your belt.
   Discovery checks protocol v1, five motors, and acknowledged writes.
4. Check the firmware build and acknowledgement count. Current firmware also
   reports applied command mode/levels and its PWM cap. “Writes acknowledged”
   means the radio received bytes; “Firmware reports commands applied” is
   stronger evidence, but neither proves a motor moved.
5. On a bench, choose a motor and tap **Briefly test selected motor**. It
   requests level 120 for a 200 ms window, then off. Test far left, left,
   center, right, far right separately. This disables belt alert forwarding.
6. Start the glasses camera and hub connection, then explicitly enable
   **Send alerts to belt**. Keep the app active. The phone relays all five
   sector levels, not its single-actuator phone patterns. The phone strength
   slider does not change belt PWM.
7. While stationary, disconnect the hub to check the belt's unavailable-input
   pattern; reconnect to check fresh-data recovery. Test phone backgrounding,
   missing frames, BLE loss and motor timeout before a supervised demo.

The hub's status continues to say its belt transport is **simulated**. The
separate **ESP32 belt · phone Bluetooth** section shows the actual local link.
The hub does not yet receive this phone-side belt telemetry. Do not interpret
healthy hub predictions as proof of belt delivery.

BLE heartbeat is capped at 15 Hz with 250 ms TTL, one write at a time. The
25 ms worker tick normally permits a write every 75 ms when acknowledgements
arrive promptly, leaving more retry margin than the former 10 Hz cap. Actual
delivery depends on the radio and is measured by firmware USB diagnostics.
Stale/invalid input
sends fault, and an expired producer lease cannot replay obstacle levels.
CoreBluetooth callbacks and a 25 ms scheduling timer run on a dedicated serial
queue, independent of the UI main thread. Telemetry snapshots update the UI
at most five times per second (failures are reported immediately). The 200 ms
producer lease starts when the UI produces intent, before it reaches the radio
queue. Delayed queued intent therefore expires rather than being refreshed.
Late delivery (over 250 ms) or stale/mismatched firmware status disarms relay
and recovers with off commands while keeping BLE connected. Three timely
acknowledged writes and fresh matching firmware status clear the warning;
re-enable alerts explicitly. An outstanding write stuck for 1 s, or no firmware
status for 3 s, disconnects; reconnect manually. **Last acknowledgement** shows
measured callback delay, including phone scheduling time. The firmware's motor
TTL remains 250 ms throughout. **Longest command gap** shows the maximum
send interval since this BLE connection began, helping identify heartbeat
starvation separately from response latency. Older firmware
without the status characteristic is explicitly acknowledgement-only; firmware
without acknowledged writes or with a different motor count is refused.

**Stop behavior:** disabling relay sends off heartbeats while connected.
Stopping the camera also disables relay. Backgrounding/locking disconnects;
the firmware may play its existing link-loss fault cue for up to 30 seconds
before going dormant, even after a best-effort off command. Turning belt power
off stops hardware immediately. Bench pulses are bounded by the command window
plus delivery/TTL behavior, not a promise of exact felt duration.

Hub pause/resume/belt-test/session-end cue timelines are not forwarded by this
first bridge; their unhealthy statuses produce the belt fault pattern. Use the
phone's explicit relay toggle and motor-test controls for this connection.
See [the protocol route notes](../docs/protocol.md#optional-iphone--belt-ble-route-2026-11-08).
No firmware update or new package dependency was introduced for this feature.
