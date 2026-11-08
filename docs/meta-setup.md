# Meta Wearables setup — HackUTD 2026

Checked live in the user's Chrome on 2026-11-07. Native Chrome computer use works after restart; browser-extension access still reports unavailable.

## Status

- Intended project: **HackUTD 26 Haptic Belt**.
- User clarified the scope: a local prototype used only by the team, with no publishing or external distribution. Use Developer Mode; skip Managed Meta registration, console organization/project creation and invitations. No terms accepted or credentials created.
- Existing Horizon developer login is separate from Wearables. Horizon team observed: **Chxrro Studio**, ID **887450296096962**. This is **not a verified Wearables organization ID**.
- Wearables project/application ID, client token and team/org ID: pending.
- No secrets obtained or written. Root `.env` is ignored by `.gitignore` and was left unchanged. No commit or push.

## Fastest prototype path: Developer Mode

The Android integration guide explicitly says app attestation is not used in Developer Mode and to set application ID and client token to `0`. The setup guide says Developer Mode lets unpublished apps register and interact with glasses without publishing review. Together, these indicate local camera-feed prototyping can proceed without first creating a Wearables console project. A Managed Meta account is required for the console organization/project/team workflow; it is distinct from the personal Meta account used with the glasses.

Sources:
- [Setup](https://wearables.developer.meta.com/docs/develop/dat/getting-started-toolkit/)
- [Android integration](https://wearables.developer.meta.com/docs/develop/dat/build-integration-android/)
- [iOS integration](https://wearables.developer.meta.com/docs/develop/dat/build-integration-ios/)

On 2026-11-07 the owner approved using the SDK for the local prototype under the [Wearables Developer Terms](https://wearables.developer.meta.com/terms/) and [Acceptable Use Policy](https://wearables.developer.meta.com/acceptable-use-policy/). The Camera Bridge target declares SDK `1.0.1`. Full Xcode is needed to resolve and build the iPhone target on this Mac.

## Verified capabilities and requirements

The official SDK README describes **developer preview**, with SDK/docs access, testing on supported glasses, and organization/release-channel sharing with testers. Do not assume unrestricted public publishing is available. The current release-channel guide describes a private invite-only channel; public publishing eligibility was not established.

Setup docs list iOS **17.2+**, Xcode **26.4+**; Android **10+**, Android Studio **Flamingo+**, Kotlin **2.2+**. Supported hardware includes Ray-Ban Meta Gen 1/2, Ray-Ban Meta Optics, Meta glasses, Meta Ray-Ban Display, Oakley Meta HSTN and Oakley Meta Vanguard. A Mock Device Kit supports simulated testing.

[Streaming docs](https://wearables.developer.meta.com/docs/develop/dat/streaming/) list high resolution as **720 × 1280**, and frame-rate options including **30 fps**. Requested quality is not guaranteed: bandwidth adaptation can reduce resolution/frame rate and increase compression. The streaming page describes Bluetooth Classic constraints; the current iOS integration page also describes Wi-Fi streaming and Local Network permission. Verify behavior for the selected SDK/device rather than treating the transport as universal.

## iOS SDK and installation

- Repository: https://github.com/meta/meta-wearables-dat-ios (the landing page's `facebook` URL redirects here).
- Samples: https://github.com/meta/meta-wearables-dat-ios/tree/main/samples
- Tags/download versions: https://github.com/meta/meta-wearables-dat-ios/tags
- API: https://wearables.developer.meta.com/docs/reference/ios_swift/dat/latest
- Integration: https://wearables.developer.meta.com/docs/develop/dat/build-integration-ios/

Camera Bridge already declares `MWDATCore` and `MWDATCamera` from SDK `1.0.1`. Open the project in Xcode to resolve the package. For a new iOS target, add those two products under **File → Add Package Dependencies**.

Follow the integration guide for Info.plist, Bluetooth/local network usage descriptions, callback handling, registration and camera permission. Developer Mode supports a custom callback URL scheme, registered in `CFBundleURLTypes`. Production configuration requires an HTTPS Universal Link, Associated Domains and AASA. The guide lists `MWDAT` configuration keys `AppLinkURLScheme`, `MetaAppID`, `ClientToken` and `TeamID`; TeamID is the Apple developer team ID, not a Meta organization ID. Do not substitute a Horizon org ID for it. [Meta’s iOS guide](https://github.com/meta/meta-wearables-dat-ios/blob/main/AGENTS.md#step-2-configure-infoplist) documents `MetaAppID=0` for Developer Mode. The [Camera Bridge project](camera-bridge.md) uses that setting and leaves the client token unset.

## Android SDK and installation

- Repository: https://github.com/meta/meta-wearables-dat-android
- Samples (including CameraAccess): https://github.com/meta/meta-wearables-dat-android/tree/main/samples
- API: https://wearables.developer.meta.com/docs/reference/android/dat/latest
- Integration: https://wearables.developer.meta.com/docs/develop/dat/build-integration-android/
- Published versions: https://central.sonatype.com/artifact/com.meta.wearable/mwdat-core/versions

SDK artifacts are on **Maven Central** under `com.meta.wearable`; no GitHub package token is needed. Include `mavenCentral()` in dependency repositories. For camera access, use the `mwdat-core` and `mwdat-camera` artifacts with the same published version; optional `mwdat-mockdevice` supports simulated testing. Repository head is labeled Release 1.0.1; confirm the version exists in Maven Central before pinning it.

Gradle dependency form: `implementation("com.meta.wearable:mwdat-core:<version>")` and `implementation("com.meta.wearable:mwdat-camera:<version>")`.

Follow the guide for Bluetooth permissions, a callback intent filter and manifest metadata `com.meta.wearable.mwdat.APPLICATION_ID` / client token. Developer Mode configuration:

```kotlin
android {
    defaultConfig {
        manifestPlaceholders["mwdat_application_id"] = "0"
        manifestPlaceholders["mwdat_client_token"] = "0"
    }
}
```

Initialize `Wearables`, launch registration with the Meta AI app, request camera permission, create a device session, wait for STARTED, then add/start the camera stream. The CameraAccess sample reads real credentials from `local.properties`; keep secrets out of tracked files. Root `.env` is the requested storage destination for any credentials obtained during console setup.

## Phone/glasses checklist

- [ ] Confirm phone OS/toolchain requirements above.
- [ ] Check the current [version dependencies](https://wearables.developer.meta.com/docs/develop/dat/version-dependencies/) for Meta AI app and glasses firmware; exact minimum versions were not transcribed.
- [ ] Connect glasses to the Meta AI app.
- [ ] Check glasses version: **Devices tab → select glasses → gear icon → General → About → Version**; update if below the supported version.
- [ ] In Meta AI on iOS or Android: open **Settings → App Info**, then tap **App version** several times to reveal **Developer Mode**. Enable it, then check the connected glasses in that screen. If Meta shows **Install** or **Update** for the on-glasses app component, wear the glasses and complete that step before starting a camera session. The phone and glasses owners perform these changes. [Meta Developer Mode troubleshooting](https://github.com/meta/meta-wearables-dat-ios/discussions/195)
- [ ] Build and install Camera Bridge on the phone.
- [ ] Register through Meta AI, grant required Bluetooth and camera permissions; on iOS follow Local Network/Wi-Fi prompts required by the integration guide.
- [ ] Confirm the app under **Meta AI settings → App connections → Developer mode apps**.
- [ ] Verify real frames arrive before connecting collision detection and belt motor commands.

## Optional console setup and teammates

[Organization onboarding](https://wearables.developer.meta.com/docs/develop/dat/onboarding-and-organization-management/) explicitly requires a **Managed Meta Account (MMA)** organization. The Wearables Register link starts **email verification → account setup → organization creation → review** at work.meta.com. The user's Horizon account did not provide a signed-in Wearables session. Do not create duplicate MMA organizations if one already exists.

After onboarding, [create a project](https://wearables.developer.meta.com/docs/develop/dat/manage-projects/) named `HackUTD 26 Haptic Belt`, with a description such as: “Mobile app streams the glasses camera to detect nearby collision hazards and sends directional vibration cues to a wearable belt.” Project names can contain letters, numbers, spaces and underscores, up to 50 characters. Configuration supports mobile app details; whether the console allows both platforms without choosing remains unverified. Stop and ask if it forces one platform.

Camera permission justification can describe the frame-based collision detector and directional haptic cues. The console requires justifications for device permissions. Real mobile identifiers, app icon and iOS HTTPS Universal Link remain to be supplied when needed.

For developers NathanS11, wxu012 and minjuna77, GitHub usernames are insufficient. Obtain their **email addresses linked to Managed Meta accounts** and ensure membership in the same MMA organization. Organization admins can invite people to create/link an MMA. In Wearables select the team → **Team → Invite member → email**. This sends an invitation email, so do not submit without explicit user approval.

Testers use a different identity flow: [private release-channel invitations](https://wearables.developer.meta.com/docs/develop/dat/set-up-release-channels/) use **personal Meta account emails**, and the guide explicitly says MMA accounts are not supported for testers. Each integration has one private channel; check its Users tab for the current tester limit. No invitations were sent.

## Next steps for the local prototype

1. Complete the phone/glasses Developer Mode checklist above.
2. Build [iPhone Camera Bridge](camera-bridge.md) and verify the test pattern first.
3. Confirm firmware and Meta AI app compatibility on the phone and glasses used for the bench test.
4. Confirm real camera frames, then integrate collision detection and belt control.

The console/team section above is reference only and outside the clarified prototype scope.
