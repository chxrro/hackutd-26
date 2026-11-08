import SwiftUI
import UIKit

@MainActor
final class BridgeModel: ObservableObject {
    enum Destination {
        case screen
        case viewer(address: String, code: String)
    }

    @Published private(set) var isSending = false
    @Published private(set) var isSettingUp = false
    @Published private(set) var sourceStatus = "Idle."
    @Published private(set) var viewerStatus = ""
    @Published private(set) var previewImage: UIImage?

    var showsLocalPreview: Bool { isSending && uploader == nil }

    private var sourceTask: Task<Void, Never>?
    private var upload: Task<Void, Never>?
    private var uploader: FrameUploader?
    private var startedAt = ContinuousClock.now
    private var lastFrameAt: ContinuousClock.Instant?
    private var runID = UUID()

    private let glasses = GlassesCamera()

    func register() {
        setup { try await self.glasses.register() }
    }

    func requestCamera() {
        setup { try await self.glasses.requestCamera() }
    }

    func openGlassesAppUpdate() {
        setup { try await self.glasses.openGlassesAppUpdate() }
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
                            self.stop(message)
                        }
                    )
                } catch {
                    guard runID == id else { return }
                    stop(error.localizedDescription)
                    return
                }
                while !Task.isCancelled {
                    let now = ContinuousClock.now
                    if let lastFrameAt, lastFrameAt.duration(to: now) >= .seconds(2) {
                        stop("Glasses feed stale. Check the connection and try again.")
                        return
                    }
                    if lastFrameAt == nil, startedAt.duration(to: now) >= .seconds(12) {
                        stop("No frames from the glasses. Check pairing and camera permission.")
                        return
                    }
                    try? await Task.sleep(for: .milliseconds(250))
                }
            }
        }
    }

    func stop(_ message: String = "Stopped.") {
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
    }

    private func offer(_ image: UIImage) {
        lastFrameAt = .now
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
