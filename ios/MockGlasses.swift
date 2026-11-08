//
// MockGlasses.swift
//
// Debug builds only. Meta's MockDeviceKit stands in for the Ray-Bans, so the
// real path (GlassesCamera -> BridgeModel -> FrameForwarder -> HubClient -> hub)
// runs without glasses. Ported from the meta/ consolidation work. The frames
// look like real ones, so the app sends `frame_source: "mock"` in phone_status
// (the hub labels them simulated), labels them everywhere as "MOCK (simulated)"
// and never shows a calm "Alerts on" while they're in use.
//
// Feed: <app Documents>/mock-feed.mp4 (HEVC) if it exists, else the phone's
// back camera. The simulator has no camera, so it needs the file.
//

#if DEBUG
import Foundation
import MWDATCore
import MWDATMockDevice

@MainActor
final class MockGlasses: ObservableObject {
    static let feedFileName = "mock-feed.mp4"

    @Published private(set) var isOn = false
    @Published private(set) var feedDescription = ""

    private var device: (any MWDATMockDevice.MockGlasses)?

    /// `prepare` configures the Meta SDK first (GlassesCamera does it lazily).
    func setOn(_ on: Bool, prepare: () throws -> Void) {
        on ? enable(prepare) : disable()
    }

    private func enable(_ prepare: () throws -> Void) {
        guard !isOn else { return }
        do {
            try prepare()
        } catch {
            feedDescription = "couldn’t configure the SDK: \(error)"
            return
        }
        let kit = MockDeviceKit.shared
        kit.enable()  // registered, permissions granted
        do {
            let glasses = try kit.pairGlasses(model: .rayBanMeta)
            if let file = Self.feedFile() {
                glasses.services.camera.setCameraFeed(fileURL: file)
                feedDescription = "video file \(file.lastPathComponent)"
            } else {
                glasses.services.camera.setCameraFeed(cameraFacing: .back)
                feedDescription = "phone back camera"
            }
            // The device is only visible to the SDK once it is on, unfolded and worn.
            glasses.powerOn()
            glasses.unfold()
            glasses.don()
            device = glasses
            isOn = true
        } catch {
            kit.disable()
            feedDescription = "couldn’t pair: \(error.description)"
        }
    }

    private func disable() {
        guard isOn else { return }
        device = nil
        MockDeviceKit.shared.disable()
        isOn = false
        feedDescription = ""
    }

    private static func feedFile() -> URL? {
        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first else { return nil }
        let url = docs.appending(path: feedFileName)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }
}
#endif
