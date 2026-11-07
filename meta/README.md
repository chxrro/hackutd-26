# meta/: iPhone app for the Meta glasses

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

## Stream settings

`GlassesController` asks for `.raw`, `.medium` (504×896) at 15 fps.
`FrameForwarder` sends at most 15 fps as JPEG (quality 0.6) and drops frames
while one is still in flight. Change them in those two files.
