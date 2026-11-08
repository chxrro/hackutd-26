# Camera Bridge

Camera Bridge is a standalone diagnostic for previewing frames in the iPhone app or a local browser. Start with the built-in test pattern, then switch to the Ray-Ban camera in Meta Developer Mode. For the integrated iPhone-to-belt pipeline, see the [Meta phone bridge](../meta/README.md).

## Run the test pattern

1. Connect the iPhone and Mac to the same trusted Wi-Fi. From the repo root on the Mac, run:

   ```sh
   python3 desktop/viewer.py --listen 0.0.0.0
   ```

   Open `http://127.0.0.1:8765` in a browser on the Mac. The terminal prints a fresh pairing code at startup. Leave the terminal running.
2. In Xcode 26.4 or newer, open `ios/CameraBridge.xcodeproj`. Xcode resolves the pinned Meta Wearables SDK 1.0.1 when it opens the project. Select your Apple development team under **Signing & Capabilities**, choose the connected iPhone (iOS 17.2 or newer), and run the **CameraBridge** scheme. If Xcode reports a bundle ID conflict, choose a unique bundle ID in the target settings. The test pattern runs without paired glasses or Meta AI registration.
3. In **Camera Bridge** on the phone, enter `http://your-mac.local:8765` and the pairing code. Use the Mac’s local hostname from **System Settings → General → Sharing**. The Mac’s private IPv4 address is another option. Tap **Send test pattern** and allow Local Network access if prompted.
4. On the Mac, look for the changing frame counter and **Test pattern · simulated source**. Tap **Stop sending** on the phone. The viewer should clear the image and show **Feed stale** after about two seconds. If the viewer never receives a frame, check the hostname, pairing code, Local Network permission, and whether the Wi-Fi permits connections between devices.

For an iOS Simulator on the same Mac, start the viewer without `--listen` and enter `http://127.0.0.1:8765` in the app.

## Connect the Ray-Ban camera

1. Pair the glasses in the Meta AI app, enable Meta Developer Mode, and follow the [phone and glasses checklist](meta-setup.md#phoneglasses-checklist). `MetaAppID` is set to `0` in `ios/Info.plist` for local Developer Mode. The project declares Meta’s Bluetooth Classic camera transport. Use a physical iPhone for the glasses check. [Meta iOS configuration](https://github.com/meta/meta-wearables-dat-ios/blob/main/AGENTS.md#step-2-configure-infoplist)
2. Run **Camera Bridge**. Tap **Connect to Meta AI** and finish registration, then tap **Allow glasses camera access**. Enter the computer address and pairing code and tap **Send glasses feed**. Keep the app in the foreground. If Camera Bridge reports that the app on the glasses needs an update, tap **Open glasses app update** and follow the Meta AI prompts.
3. For a stationary bench test, wear the glasses and look at a consented test target. Meta says some glasses reject session start while unworn. Confirm movement in the browser, then fold or disconnect the glasses and confirm the viewer clears. Record the phone and glasses models, firmware, and observed frame rate and delay before connecting inference. [Meta session troubleshooting](https://github.com/meta/meta-wearables-dat-ios/issues/265)

## Mirror the preview over USB

Connect the iPhone to the Mac over USB and mirror its screen in Xcode's Device Hub. In Camera Bridge, tap **Preview test pattern** and look for the changing frame number in the mirrored window. After connecting to Meta AI and granting camera access, wear the glasses and tap **Preview glasses feed**. This displays frames in the app, so the mirrored window works without a computer viewer address, pairing code, or hotspot. Tap **Stop preview** to end the session. The browser viewer is a separate mode for receiving JPEG frames.

## Preview behavior

One phone sends JPEGs to `POST /frame` with its pairing code and a `test` or `glasses` source label. The sender skips frames while an upload is in flight. The viewer keeps one frame in memory, clears the display after two seconds without a new frame, and serves the preview only on the computer. That timeout measures time since receipt. Use a trusted network because the preview uses local HTTP. The app stores no recordings.

To check an unsigned simulator build from the repo root with full Xcode:

```sh
xcodebuild -project ios/CameraBridge.xcodeproj -scheme CameraBridge \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
```
