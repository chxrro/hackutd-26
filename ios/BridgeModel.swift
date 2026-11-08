import SwiftUI
import UIKit

@MainActor
final class BridgeModel: ObservableObject {
    enum Destination {
        case screen
        case viewer(address: String, code: String)
        /// The haptic hub over WebSocket (docs/protocol.md section 1). Frames go to `onFrame`.
        case hub
    }

    enum DestinationKind: Equatable {
        case screen, viewer, hub
    }

    @Published private(set) var isSending = false
    @Published private(set) var isSettingUp = false
    @Published private(set) var sourceStatus = "Idle."
    @Published private(set) var viewerStatus = ""
    @Published private(set) var previewImage: UIImage?
    @Published private(set) var activeDestination: DestinationKind?
    @Published private(set) var activeSource: FrameSource?
    /// Why the source stopped by itself (nil after a user stop or a new start).
    @Published private(set) var failure: String?
    /// Where the failed run was sending, so the wearer screen only reports hub runs.
    @Published private(set) var failedDestination: DestinationKind?
    @Published private(set) var glassesSetup: GlassesCamera.SetupStatus?
    /// Set when the glasses report that their app needs an update.
    @Published private(set) var glassesNeedUpdate = false

    var showsLocalPreview: Bool { isSending && activeDestination == .screen }

    /// Receives every frame while the destination is `.hub`.
    var onFrame: ((UIImage) -> Void)?
    /// Receives the stream state for `phone_status` while the destination is `.hub`.
    var onStreamState: ((String) -> Void)?

    /// When the last frame arrived from the source, nil before the first.
    var lastFrame: ContinuousClock.Instant? { lastFrameAt }
    /// Frames received from the source since launch, for diagnostics.
    private(set) var framesReceived = 0

    private var sourceTask: Task<Void, Never>?
    private var upload: Task<Void, Never>?
    private var uploader: FrameUploader?
    private var startedAt = ContinuousClock.now
    private var lastFrameAt: ContinuousClock.Instant?
    private var runID = UUID()

    private var lastPreviewAt: ContinuousClock.Instant?

    private let glasses = GlassesCamera()

    #if DEBUG
    /// Debug-only stand-in glasses (Meta MockDeviceKit). See MockGlasses.swift.
    let mockGlasses = MockGlasses()

    /// Debug test fault: keep capturing but stop forwarding frames to the hub,
    /// so the hub reports "no camera frames" while the phone still has them.
    @Published var holdFramesToHub = false

    func setMockGlasses(_ on: Bool) {
        guard !isSending else { return }
        mockGlasses.setOn(on) { try self.glasses.configure() }
        refreshGlassesSetup()
    }
    #endif

    /// True when glasses frames come from Debug mock glasses, not the Ray-Bans.
    var usingMockGlasses: Bool {
        #if DEBUG
        return mockGlasses.isOn
        #else
        return false
        #endif
    }

    func register() {
        setup { try await self.glasses.register() }
    }

    func requestCamera() {
        setup { try await self.glasses.requestCamera() }
    }

    func openGlassesAppUpdate() {
        setup {
            let message = try await self.glasses.openGlassesAppUpdate()
            self.glassesNeedUpdate = false
            return message
        }
    }

    /// Reads registration, camera permission and device state without prompting.
    func refreshGlassesSetup() {
        guard !isSettingUp else { return }
        Task {
            do {
                glassesSetup = try await glasses.setupStatus()
            } catch {
                sourceStatus = error.localizedDescription
            }
        }
    }

    func handleCallback(_ url: URL) {
        Task {
            do {
                if let message = try await glasses.handleCallback(url) { sourceStatus = message }
            } catch {
                sourceStatus = error.localizedDescription
            }
        }
    }

    private func setup(_ action: @escaping @MainActor () async throws -> String) {
        isSettingUp = true
        Task {
            defer { isSettingUp = false }
            do {
                sourceStatus = try await action()
            } catch {
                sourceStatus = error.localizedDescription
            }
            if let status = try? await glasses.setupStatus() { glassesSetup = status }
        }
    }

    func start(_ source: FrameSource, destination: Destination) {
        guard !isSending, !isSettingUp else { return }
        if case let .viewer(address, code) = destination {
            do {
                uploader = try FrameUploader(address: address, code: code, source: source)
            } catch {
                sourceStatus = error.localizedDescription
                return
            }
        }

        isSending = true
        viewerStatus = "Viewer: waiting for the first frame."
        startedAt = .now
        lastFrameAt = nil
        failure = nil
        activeSource = source
        switch destination {
        case .screen: activeDestination = .screen
        case .viewer: activeDestination = .viewer
        case .hub: activeDestination = .hub
        }
        reportStream("starting")
        // The hub path warns after 2 s without frames (WearerStatus) and keeps
        // waiting a while for the glasses to recover; the viewer paths keep the
        // original 2 s cut-off.
        let staleLimit: Duration = activeDestination == .hub ? .seconds(10) : .seconds(2)

        switch source {
        case .test:
            sourceStatus = "Test pattern · simulated source"
            sourceTask = Task {
                var frame = 0
                while !Task.isCancelled {
                    frame += 1
                    offer(Self.testImage(frame))
                    try? await Task.sleep(for: .milliseconds(200))
                }
            }
        case .glasses:
            sourceStatus = "Connecting to Ray-Ban glasses…"
            let id = runID
            sourceTask = Task { [self] in
                do {
                    try await glasses.start(
                        onFrame: { [weak self] image in
                            guard let self, self.runID == id else { return }
                            self.sourceStatus = "Ray-Ban camera frames"
                            self.offer(image)
                        },
                        onFailure: { [weak self] message in
                            guard let self, self.runID == id else { return }
                            self.fail(message)
                        }
                    )
                } catch {
                    guard runID == id else { return }
                    fail(error.localizedDescription)
                    return
                }
                while !Task.isCancelled {
                    let now = ContinuousClock.now
                    if let lastFrameAt, lastFrameAt.duration(to: now) >= staleLimit {
                        fail("Glasses feed stale. Check the connection and try again.")
                        return
                    }
                    if lastFrameAt == nil, startedAt.duration(to: now) >= .seconds(12) {
                        fail("No frames from the glasses. Check pairing and camera permission.")
                        return
                    }
                    try? await Task.sleep(for: .milliseconds(250))
                }
            }
        }
    }

    func stop(_ message: String = "Stopped.") {
        let wasSending = isSending
        runID = UUID()
        sourceTask?.cancel()
        sourceTask = nil
        upload?.cancel()
        upload = nil
        uploader = nil
        previewImage = nil
        glasses.stop()
        isSending = false
        sourceStatus = message
        failure = nil
        failedDestination = nil
        if wasSending { reportStream("stopped") }
        activeDestination = nil
        activeSource = nil
    }

    /// The source stopped by itself (or was interrupted): keep the reason for the wearer screen.
    func fail(_ message: String) {
        let destination = activeDestination
        stop(message)
        failure = message
        failedDestination = destination
        if message == GlassesCamera.updateRequiredMessage { glassesNeedUpdate = true }
    }

    private func reportStream(_ state: String) {
        if activeDestination == .hub { onStreamState?(state) }
    }

    private func offer(_ image: UIImage) {
        if lastFrameAt == nil { reportStream("streaming") }
        lastFrameAt = .now
        framesReceived += 1
        if activeDestination == .hub {
            #if DEBUG
            if !holdFramesToHub { onFrame?(image) }
            #else
            onFrame?(image)
            #endif
            // The helper's live preview doesn't need every frame.
            let now = ContinuousClock.now
            if lastPreviewAt.map({ $0.duration(to: now) >= .milliseconds(200) }) ?? true {
                lastPreviewAt = now
                previewImage = image
            }
            return
        }
        guard let uploader else {
            previewImage = image
            return
        }
        // Keep one request in flight and drop frames while the network is busy.
        guard upload == nil else { return }
        guard let jpeg = image.jpegData(compressionQuality: 0.55) else {
            viewerStatus = "Viewer: could not encode the frame."
            return
        }

        let id = runID
        upload = Task {
            do {
                try await uploader.send(jpeg)
                guard runID == id else { return }
                viewerStatus = "Viewer: receiving frames."
            } catch {
                guard runID == id else { return }
                viewerStatus = "Viewer: \(error.localizedDescription)"
            }
            upload = nil
        }
    }

    private static func testImage(_ frame: Int) -> UIImage {
        let size = CGSize(width: 360, height: 640)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            UIColor(red: 0.09, green: 0.15, blue: 0.25, alpha: 1).setFill()
            context.fill(CGRect(origin: .zero, size: size))
            UIColor.systemTeal.setFill()
            context.fill(CGRect(x: 20 + (frame % 32) * 10, y: 288, width: 10, height: 80))
            ("TEST PATTERN\nFrame \(frame)" as NSString).draw(
                at: CGPoint(x: 20, y: 72),
                withAttributes: [
                    .font: UIFont.monospacedSystemFont(ofSize: 28, weight: .semibold),
                    .foregroundColor: UIColor.white,
                ]
            )
        }
    }
}
