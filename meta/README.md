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
| App, Belt | **levels**: green "Alerts active", red bars of varying height | **fault**: orange "Not reliable: <reason>", flat orange bars. "No status from the hub" = no status for 2 s: treat it as a fault |
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
  Navigation/   experimental campus route: RouteGuidance (pure), CampusNavigator, CampusRouteView, route GeoJSON
  Resources/    Info.plist (MWDAT keys), entitlements
HapticBeltTests/ protocol golden-vector + route guidance tests (no SDK, no device needed)
project.yml      XcodeGen spec (the .xcodeproj is generated, not committed)
Config/          Base.xcconfig + your gitignored Local.xcconfig
```

## Campus route (experimental)

Spoken walking directions on a fixed campus route: **ECSW ↔ Comets LANding**
(the esports center in the Student Union). Open it from **Campus route
(experimental)** at the bottom of the main screen. Design and reasoning:
[docs/navigation-research.md §11](../docs/navigation-research.md).

> Supervised demo only. Walk with a sighted guide. Directions say where the
> route goes, not whether it's clear. Route speech goes quiet whenever the hub
> reports an obstacle, a fault or an unhealthy state. When no hub is connected,
> the screen says in red that obstacle alerts are **not** running.

- **Route:** `HapticBelt/Navigation/ecsw-to-comets-landing.geojson`, from
  OpenStreetMap (ODbL), about 320 m, **not yet walked**. To use UTD's map data
  instead, replace the GeoJSON LineString (`[lon, lat]` order). Optional
  properties: `name`, `destination`, `attribution`. The reverse route is
  generated.
- **Facing from:** *Compass* means the phone is held flat with its top
  pointing where you face. *Walking* means the GPS direction of travel, which
  needs a few steady steps (phone in a pocket or on a lanyard). *Auto* uses the
  compass when its accuracy is ≤ 35°, otherwise walking.
- **Phrases** all start with "Route:", for example "Route: bear left, 120
  metres to Comets LANding." The phone never vibrates for route directions; in
  this app vibration means obstacles.
- **No direction** is given when GPS is worse than ±25 m, the fix is older
  than 5 s, or the facing direction is unknown. The screen shows the reason.
- **Simulate the walk:** a pretend walker that starts facing away, wanders off
  the path once, and arrives. Labelled SIMULATED on screen. Use it in the
  Simulator or as a video fallback, never as evidence of a real walk.
- **Add 40 m straight test from here:** a straight route from where you stand,
  in the direction the phone faces. Use it for a quick outdoor check anywhere.
  The first tap warms up GPS and the compass. Tap again a few seconds later.
- **Privacy:** location is used in memory only. It isn't logged, saved or sent
  to the hub. iOS asks for when-in-use location permission the first time.

**Checks**

```bash
xcodegen generate
xcodebuild test -project HapticBelt.xcodeproj -scheme HapticBelt \
  -destination 'platform=iOS Simulator,name=iPhone 16' -only-testing:HapticBeltTests
```

`RouteGuidanceTests` runs the route logic hostless, including a simulated walk
of the real route in both directions.

**First real test (people, not agents)**

1. Outdoors near ECSW. Use **Add 40 m straight test from here**: face along
   it, then sideways, then backwards. Check that you hear straight on, turn
   left or right, and turn around.
2. Start the hub and connect the phone, glasses camera running. Walk ECSW →
   Comets LANding with a sighted guide, phone held flat, wearer **not**
   blindfolded. Note where GPS drops above ±25 m (near buildings) and where
   the doors really are.
3. Have someone step in front of the camera mid-route. Route speech should
   stop at once and resume about 2.5 s after the alert clears.
4. Only then: the same walk for the video, guide alongside.

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

## Stream settings

`GlassesController` asks for `.raw`, `.medium` (504×896) at 15 fps.
`FrameForwarder` sends at most 15 fps as JPEG (quality 0.6) and drops frames
while one is still in flight. Change them in those two files.
