# Camera Bridge (iPhone app)

Camera Bridge (`ios/`) is the team's iPhone app for the Ray-Ban Meta glasses and the haptic belt. Ethan Hur built it as a standalone camera diagnostic (commit `3e8d956`). The owner chose it as the user-facing app, and it now streams the glasses camera to the hub over the WebSocket protocol in [docs/protocol.md](protocol.md). His HTTP viewer path is still available under **Setup → Advanced: diagnostic viewer**.

This is an experimental collision-alert aid for a supervised prototype. It does not replace a cane, a guide dog or a sighted guide. **Silence does not mean the path is clear.**

## Two screens

- **Belt alerts** (the default, for the person wearing the belt) has one large **Start/Stop** button and one sentence of state, built for VoiceOver first. While a control code is set it also has **Pause/Resume**.
  - "Alerts on" appears only when the hub link is up, a status arrived in the last 2 s, the hub reports `healthy: true` with `mode: "levels"`, the belt is connected, and frames are real camera frames.
  - Everything else is a black-on-yellow warning, never calm: hub not connected, no status from hub, any hub fault reason, no camera frames, paused, belt test, belt connecting or disconnected, and simulated input or output (test pattern, mock glasses, simulated belt).
  - **Paused** reads "Paused. Alerts are off; the belt pulses softly every 15 seconds as a reminder." The hub plays a pause cue and then a soft reminder pulse (protocol "Belt cues"; 15 s is the hub default, `--pause-reminder` can change it or turn it off). A real fault while paused outranks the pause, as on the hub: the headline becomes e.g. "Warning: no camera frames (alerts paused)" and is announced as "Warning: no camera frames. Alerts paused." When it clears while still paused: "Recovered. Still paused. Alerts are off."
  - After **Resume** the hub reports `reason: "resuming alerts"` for about 0.6 s while it plays the resume cue. The screen shows "Resuming alerts", nothing is announced, and the next state is announced once ("Resumed. Alerts on."). If it still says "resuming alerts" after 2 s, the app warns: "Warning: alerts not resumed".
  - Belt link: "Warning: belt disconnected" and "Warning: belt connecting" are one state for announcements, whether the hub reports them in `reason` (`healthy: false`, newer hubs) or only in `belt` (older hubs), so an automatic reconnect (disconnected, connecting, connected) is announced once and then "Belt connected. Alerts on." `reason: "belt not acknowledging"` (link up, belt not confirming commands) reads "Warning: belt not responding". As on the hub, a camera or perception fault outranks belt reasons, and a down link outranks not acknowledging.
  - VoiceOver announcements cover important changes only: fault plus its reason, recovered, paused/resumed, belt disconnected/connected, stopped. They are de-duplicated and rate-limited to one every 3 s. A held announcement is replaced by a newer one, or dropped if the state goes back to what was last spoken. The phone gives a haptic tap for each announcement. Every announcement is also logged under subsystem `com.hackutd26.CameraBridge`, category `announce`.
  - Optional obstacle-kind speech ("Person ahead", "Object far left") is **off by default**. It names the kind of the strongest vibrating motor's label from `sectors[i].kind`, at most once every 4 s, and repeats the same phrase at most once every 15 s. It describes where an obstacle is. It never says where to walk, and it never says that the path is clear.
  - **Stop** pauses the hub first (if the control code is set), so the belt plays the pause cue. Either way the hub then sees frames stop, and its "no camera frames" fault outranks the pause, so the belt plays the fault pattern. **Start** resumes the hub if it is paused. If the app goes into the background, it stops the camera without pausing, so the hub plays the fault pattern.
- **Setup** (for a sighted helper) has the following sections:
  - **Hub:** hub pairing.
  - **Connection:** diagnostics, including the link, status age, hub health, belt, frames per second in and sent, control-code state, and recent announcements.
  - **Glasses:** glasses setup.
  - **Hub settings:** the classifier, alert range and belt test.
  - **Recording:** session recording, with a consent prompt before anything is sent.
  - **Live preview**, shown while running.

## Pair with the hub

1. On the laptop, in `hub/`, run the hub with the glasses source, for example `uv run haptic-hub run --source glasses`. It prints the address to use and, on hub builds with the Control layer, a 16-character **control code**. The code changes every time the hub restarts.
2. In **Setup → Hub**, enter the address (`192.168.1.20`, `192.168.1.20:8765` or `ws://laptop.local:8765`) and the control code, then tap **Save and connect**. Addresses must be local: `.local` or a private/loopback IPv4 address, the same rule as the viewer uploader. Leave the code empty to send frames only. Pause, settings, belt test and recording then stay off.
3. **Connection → Control code** should read **Accepted by the hub**. The app sends `get` on every connect, because pairing belongs to one WebSocket connection. **Rejected** means the code is wrong or the hub restarted. **No reply** means the hub predates the Control layer.

The address is stored in UserDefaults, and the code in the Keychain for this device only.

## Connect the Ray-Ban glasses

1. Pair the glasses in Meta AI and enable Developer Mode, following the [phone and glasses checklist](meta-setup.md#phoneglasses-checklist). `MetaAppID` is `0` in `ios/Info.plist` for local Developer Mode. The plist declares Meta's Bluetooth Classic camera transport (`external-accessory`, `com.meta.ar.wearable`); don't remove it. [Meta iOS configuration](https://github.com/meta/meta-wearables-dat-ios/blob/main/AGENTS.md#step-2-configure-infoplist)
2. In Xcode 26.4 or newer, open `ios/CameraBridge.xcodeproj`, choose your team under **Signing & Capabilities** (or put `DEVELOPMENT_TEAM` in the gitignored `ios/Signing.local.xcconfig`), and run the **CameraBridge** scheme on the iPhone (iOS 17.2+).
3. In **Setup → Glasses**, tap **1. Connect to Meta AI**, then **2. Allow glasses camera access**. Each row shows its status, and **Check glasses status** refreshes them without prompting. If the glasses report that their app needs an update, row 3 changes to **Needed**. Wear the glasses and tap **3. Open glasses app update**.
4. Put the glasses on, then press **Start** on the main screen. Keep the app in the foreground. The screen stays awake while running.

## Test without glasses (Debug builds only)

**Setup → Development: MOCK (simulated)** has a **Use mock glasses** switch. It uses Meta's MockDeviceKit, so frames take the normal camera path into the hub. The wearer screen then shows a **MOCK GLASSES (simulated)** banner and never shows "Alerts on". The app tells the hub with `frame_source: "mock"` in `phone_status` (`docs/protocol.md`), so the hub's status, log, dashboard and recordings label those frames as simulated; the test pattern is sent as `test_pattern`. Keep mock runs out of any measurement anyway. **Hold frames to hub (test fault)** keeps the camera running but stops sending frames, so the hub reports a fault.

The mock feed is `mock-feed.mp4` (HEVC) in the app's Documents folder, or the phone's back camera if there is no file. The simulator has no camera, so make a clip:

```sh
ffmpeg -f lavfi -i testsrc2=size=504x896:rate=15 -t 60 -c:v libx265 -tag:v hvc1 -pix_fmt yuv420p mock-feed.mp4
```

The simulator build must be signed to run locally, which is Xcode's default for simulators and needs no team. The Meta SDK and the Keychain both need it. A build with `CODE_SIGNING_ALLOWED=NO` installs, but turning on mock glasses fails with `WearablesError` and the control code isn't saved.

```sh
xcodebuild -project ios/CameraBridge.xcodeproj -scheme CameraBridge \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro' -derivedDataPath build/ios CODE_SIGN_IDENTITY=- build
xcrun simctl install booted build/ios/Build/Products/Debug-iphonesimulator/CameraBridge.app
cp mock-feed.mp4 "$(xcrun simctl get_app_container booted com.hackutd26.CameraBridge data)/Documents/"
```

Run the hub with `uv run haptic-hub run --source glasses --belt sim --perception sweep`. In the app, set the hub address to `127.0.0.1:8765`, turn on mock glasses, and press Start. The hub log switches from `belt FAULT` to `belt LEVELS` and says `frame source: mock, SIMULATED, NOT the glasses`. The wearer screen reads **Warning: simulation only** (mock glasses and simulated belt).

## Checks

```sh
# Unit tests: protocol golden vectors, state -> sentence/announcement mapping, pairing validation.
xcodebuild -project ios/CameraBridge.xcodeproj -scheme CameraBridge \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro' CODE_SIGN_IDENTITY=- test

# Unsigned device build (no mock code: MockGlasses is #if DEBUG only).
xcodebuild -project ios/CameraBridge.xcodeproj -scheme CameraBridge -configuration Release \
  -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

The tests are a hostless bundle that compiles the SDK-free files directly (`ios/Hub`, `ios/Wearer`, `FrameUploader.swift`).

## Advanced: diagnostic viewer

Ethan's original computer viewer still works for checking frames without the hub. It is separate from the hub and the belt.

1. Connect the iPhone and the Mac to the same trusted Wi-Fi. From the repo root on the Mac, run `python3 desktop/viewer.py --listen 0.0.0.0` and open `http://127.0.0.1:8765`. The terminal prints a fresh pairing code.
2. In Camera Bridge, open **Setup → Advanced: diagnostic viewer**. Enter `http://your-mac.local:8765` and the pairing code, then tap **Send test pattern** or **Send glasses feed**. Allow Local Network access if prompted.
3. On the Mac, look for the changing frame counter. **Stop sending** clears the viewer after about 2 s (**Feed stale**). If no frame arrives, check the hostname, pairing code, Local Network permission, and whether the Wi-Fi allows device-to-device traffic.

**Preview test pattern** and **Preview glasses feed** show frames on the phone screen instead, for Device Hub mirroring over USB. In the simulator, start the viewer without `--listen` and use `http://127.0.0.1:8765`.

The viewer keeps one frame in memory, clears it after 2 s without a new one, and serves the preview only on the computer. The sender skips frames while an upload is in flight. The app stores no recordings.
