# Camera Bridge (iPhone app)

Camera Bridge (`ios/`) is the team's iPhone app for the Ray-Ban Meta glasses and the haptic belt. Ethan Hur built it as a standalone camera diagnostic (commit `3e8d956`). The owner chose it as the user-facing app, and it now streams the glasses camera to the hub over the WebSocket protocol in [docs/protocol.md](protocol.md). His HTTP viewer path is still available under **Setup → Advanced: diagnostic viewer**.

This is an experimental collision-alert aid for a supervised prototype. It does not replace a cane, a guide dog or a sighted guide. **Silence does not mean the path is clear.**

## App screens

- **Belt alerts** is the wearer screen. The large **Start alerts** or **Stop alerts** button stays at the top. **Hear status** reads the current state using VoiceOver or iPhone speech. Connection warnings link directly to the hub, glasses, or belt checks. With a control code set, the screen also offers **Pause alerts** and **Resume alerts**.
  - "Alerts on" appears only when the hub link is up, a status arrived in the last 2 s, the hub reports `healthy: true`, the belt is connected, and frames are real camera frames. A healthy hub can send `mode: "off"` with all motor levels zero between obstacles.
  - Everything else is a black-on-yellow warning, never calm: hub not connected, no status from hub, any hub fault reason, no camera frames, paused, belt test, belt connecting or disconnected, and simulated input or output (test pattern, mock glasses, simulated belt).
  - **Paused** reads "Paused. Alerts are off; the belt pulses softly every 15 seconds as a reminder." The hub plays a pause cue and then a soft reminder pulse (protocol "Belt cues"; 15 s is the hub default, `--pause-reminder` can change it or turn it off). A real fault while paused outranks the pause, as on the hub: the headline becomes e.g. "Warning: no camera frames (alerts paused)" and is announced as "Warning: no camera frames. Alerts paused." When it clears while still paused: "Recovered. Still paused. Alerts are off."
  - After **Resume** the hub reports `reason: "resuming alerts"` for about 0.6 s while it plays the resume cue. The screen shows "Resuming alerts", nothing is announced, and the next state is announced once ("Resumed. Alerts on."). If it still says "resuming alerts" after 2 s, the app warns: "Warning: alerts not resumed".
  - Belt link: "Warning: belt disconnected" and "Warning: belt connecting" are one state for announcements, whether the hub reports them in `reason` (`healthy: false`, newer hubs) or only in `belt` (older hubs), so an automatic reconnect (disconnected, connecting, connected) is announced once and then "Belt connected. Alerts on." `reason: "belt not acknowledging"` (link up, belt not confirming commands) reads "Warning: belt not responding". As on the hub, a camera or perception fault outranks belt reasons, and a down link outranks not acknowledging.
  - VoiceOver announcements cover important changes only: fault plus its reason, recovered, paused/resumed, belt disconnected/connected, stopped by the wearer (with or without the hub told), alerts stopped by a failure. They are de-duplicated and rate-limited to one every 3 s. A held announcement is replaced by a newer one, or dropped if the state goes back to what was last spoken. The phone gives a haptic tap for each announcement. Every announcement is also logged under subsystem `com.hackutd26.CameraBridge`, category `announce`.
  - [Spoken obstacle alerts](#spoken-obstacle-alerts) are **off by default**. Routine alerts are spaced at least 4 s apart. Urgent alerts can interrupt routine speech, with at least 2 s between urgent alerts. The same type, direction and urgency are repeated no more often than every 15 s.
  - **Stop** tells the hub first (`end_session`, protocol "Deliberate stop vs. lost phone"), then stops the camera. The app sends no frame after `end_session`. A hub that accepts it plays the session-ended cue (three soft pulses on the inner motors) and stays quiet; the screen says **"Stopped"** and VoiceOver says "Stopped. The belt will not alert." once. While the hub's reply is pending the screen says "Stopping" and nothing is spoken. If the hub wasn't told (no control code, hub not connected, refused, no reply, or an older hub), the hub can't tell the stop from a lost phone and plays the fault pattern: the screen warns "Stopped. Hub not told" and VoiceOver says "Stopped. The hub wasn’t told, so the belt plays its fault pattern." If a later status says the hub is no longer stopped (for example, it restarted), the app warns "Warning: hub no longer stopped".
  - **Start** ends the stop on the hub (`phone_status` `"starting"`, then frames). The hub plays the fault pattern until the first frame arrives, then the resume cue, then normal alerts. Start also resumes the hub if it is still paused.
  - **Backgrounding** the app (or the stream failing by itself) never sends `end_session`: the camera stops and the hub plays the fault pattern, as for a lost phone.
- **Setup** (for a sighted helper) has the following sections:
  - **Hub:** hub pairing.
  - **Connection:** diagnostics, including the link, status age, hub health, belt, frames per second in and sent, control-code state, and recent announcements.
  - **Glasses:** glasses setup.
  - **Hub settings:** the classifier, alert range and belt test.
  - **Recording:** session recording, with a consent prompt before anything is sent.
  - **Live preview**, shown while running.
- **Practice alerts** opens from **Belt alerts** while the camera is idle.

## Camera preview

**Setup → Camera preview** opens the Ray-Ban view. **Start glasses preview** shows video on the phone without starting a belt session. In a running belt session, **Camera preview** shows the same feed. The preview labels simulated input, clears stale frames, and describes the camera state for VoiceOver.

## Use the app with iOS accessibility

- **VoiceOver.** The wearer can navigate to **Start alerts**, **Stop alerts**, **Pause alerts**, or **Resume alerts** and double-tap to activate. Use **Hear status** or **Read belt status** in the primary button's VoiceOver Actions to hear the current state again. The app also announces significant state changes and warnings. [Apple’s VoiceOver guidance](https://developer.apple.com/design/human-interface-guidelines/voiceover)
- **Voice Control.** With iOS Voice Control on, use the visible control name, such as “Tap Start alerts” or “Tap Camera preview.” The primary belt button also accepts “Tap Start belt” and “Tap Stop belt” in their respective states. Pause and Resume follow the same naming pattern. [Apple’s accessibility guidance](https://developer.apple.com/design/human-interface-guidelines/accessibility)
- **Larger text and touch targets.** The main actions scale with Dynamic Type. Start has a large touch area, and the glasses setup rows stack at accessibility text sizes. Warnings use words and symbols alongside color. [Apple’s button guidance](https://developer.apple.com/design/human-interface-guidelines/buttons)
- **Dim Flashing Lights.** When enabled in iOS, the app hides raw on-screen preview video and describes why. Camera processing for a belt session can still run. [Apple’s preview accessibility setting](https://developer.apple.com/documentation/swiftui/environmentvalues/accessibilitydimflashinglights)

For a supervised accessibility check, use a physical iPhone with simulated input and a simulated belt. With VoiceOver and the screen curtain on, start, pause, resume and stop, read status on demand, and check that an interrupted feed is announced. With Voice Control, repeat the actions by name. Check the same actions separately with Switch Control and Full Keyboard Access. [Apple’s testing guide](https://developer.apple.com/documentation/accessibility/performing-accessibility-testing-for-your-app)

## Spoken obstacle alerts

In **Setup → VoiceOver and haptics**, turn on **Speak obstacle alerts**. With `--perception vision`, the app announces the strongest current hazard. It uses “Person” or “Object” when a confident detection matches the hazard. It uses “Obstacle” when depth shows a hazard and the app lacks a matching label. Distant objects and uncertain detections alone stay quiet. When box growth or optional `--motion flow` shows the gap closing, speech can say “Person ahead, getting closer.” Distance estimates and approach cues still need real hardware validation.

The app uses VoiceOver when it’s enabled and iOS speech synthesis otherwise. Speech follows the iPhone’s audio output. An urgent alert starts with “Caution.” Connection and other fault warnings have higher VoiceOver priority. Simulated sessions are identified in speech. On older hubs, speech uses obstacle type and direction only. [Spoken status format](protocol.md#optional-spoken-obstacle-status-hub--iphone)

## Where am I?

Tap **Where am I?** on the wearer screen, or say “Tap Where am I” with iOS Voice Control enabled. The first tap can ask for location permission while alerts are off. Each tap requests one location fix and uses Apple’s place lookup. The app can give a nearby address or a broader area, depending on the location accuracy. Wayfeel keeps the result briefly on the phone and excludes it from the hub and announcement log.

If an obstacle or a fault is active, the result is available on the button as its VoiceOver value. Automatic speech waits for a quiet period and expires if it can’t speak promptly. A new obstacle or fault announcement can interrupt a location reply. Meta AI’s built-in glasses assistant is separate from this control. [Apple’s one-time location request](https://developer.apple.com/documentation/corelocation/cllocationmanager/requestlocation())

## Practice alerts

Open **Practice alerts** from the wearer screen while stationary with live alerts stopped. **Play sample warning** reads an example on the phone. With a paired hub and a connected belt, choose **Play left cue**, **Play right cue**, or **Play connection-loss cue**. Left and right identify the hazard's side. The connection-loss example plays the belt's double pulse without disconnecting it. Each example ends automatically. A simulated belt reports the requested cue without vibrating. [Practice command](protocol.md#control-websocket-text-json-additive-draft-2026-11-07)

## Pair with the hub

1. On the laptop, in `hub/`, run the hub with the glasses source, for example `uv run haptic-hub run --source glasses`. It prints the address to use and, on hub builds with the Control layer, a 16-character **control code**. The code changes every time the hub restarts.
2. In **Setup → Hub**, enter the address (`192.168.1.20`, `192.168.1.20:8765` or `ws://laptop.local:8765`) and the control code, then tap **Save and connect**. Addresses must be local: `.local` or a private/loopback IPv4 address, the same rule as the viewer uploader. Leave the code empty to send frames only. Pause, settings, belt test and recording then stay off.
3. **Connection → Control code** should read **Accepted by the hub**. The app sends `get` on every connect, because pairing belongs to one WebSocket connection. **Rejected** means the code is wrong or the hub restarted. **No reply** means the hub predates the Control layer.

The address is stored in UserDefaults, and the code in the Keychain for this device only.

## Connect the Ray-Ban glasses

1. Pair the glasses in Meta AI and enable Developer Mode, following the [phone and glasses checklist](meta-setup.md#phoneglasses-checklist). `MetaAppID` is `0` in `ios/Info.plist` for local Developer Mode. The plist declares Meta's Bluetooth Classic camera transport (`external-accessory`, `com.meta.ar.wearable`); don't remove it. [Meta iOS configuration](https://github.com/meta/meta-wearables-dat-ios/blob/main/AGENTS.md#step-2-configure-infoplist)
2. In Xcode 26.4 or newer, open `ios/CameraBridge.xcodeproj`, choose your team under **Signing & Capabilities** (or put `DEVELOPMENT_TEAM` in the gitignored `ios/Signing.local.xcconfig`), and run the **CameraBridge** scheme on the iPhone (iOS 17.2+).
3. In **Setup → Glasses**, tap **1. Connect to Meta AI**, then **2. Allow glasses camera access**. Each row shows its status, and **Check glasses status** refreshes them without prompting. If the glasses report that their app needs an update, row 3 changes to **Needed**. Wear the glasses and tap **3. Open glasses app update**.
4. Put the glasses on, then press **Start alerts** on the main screen. Keep the app in the foreground. The screen stays awake while running.

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

Run the hub with `uv run haptic-hub run --source glasses --belt sim --perception sweep`. In the app, set the hub address to `127.0.0.1:8765`, turn on mock glasses, and press **Start alerts**. The hub log switches from `belt FAULT` to `belt LEVELS` and says `frame source: mock, SIMULATED, NOT the glasses`. The wearer screen reads **Warning: simulation only** (mock glasses and simulated belt). For a quick check without a mock video file, select **Test pattern (simulated)** under **Setup → Glasses**. The sweep generates synthetic belt levels and has no detections with distance or motion to announce.

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

From `hub/`, run `uv sync --frozen` and `uv run pytest tests/test_spoken_obstacle.py` to check same-detection depth and motion, and suppression during stale data or belt cues. The iOS tests cover the resulting speech and status compatibility with older hubs. [Hub test setup](../hub/README.md)

## Advanced: diagnostic viewer

Ethan's original computer viewer still works for checking frames without the hub. It is separate from the hub and the belt.

1. Connect the iPhone and the Mac to the same trusted Wi-Fi. From the repo root on the Mac, run `python3 desktop/viewer.py --listen 0.0.0.0` and open `http://127.0.0.1:8765`. The terminal prints a fresh pairing code.
2. In Camera Bridge, open **Setup → Advanced: diagnostic viewer**. Enter `http://your-mac.local:8765` and the pairing code, then tap **Send test pattern** or **Send glasses feed**. Allow Local Network access if prompted.
3. On the Mac, look for the changing frame counter. **Stop sending** clears the viewer after about 2 s (**Feed stale**). If no frame arrives, check the hostname, pairing code, Local Network permission, and whether the Wi-Fi allows device-to-device traffic.

**Preview test pattern** and **Preview glasses feed** show frames on the phone screen instead, for Device Hub mirroring over USB. In the simulator, start the viewer without `--listen` and use `http://127.0.0.1:8765`.

The viewer keeps one frame in memory, clears it after 2 s without a new one, and serves the preview only on the computer. The sender skips frames while an upload is in flight. The app stores no recordings.
