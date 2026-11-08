import MWDATCore
import MWDATCamera
import UIKit

@MainActor
final class GlassesCamera {
    private var configured = false
    private var selector: AutoDeviceSelector?
    private var session: DeviceSession?
    private var camera: Camera?
    private var listeners = ListenerTokenBag()
    private var observation: Task<Void, Never>?
    private var sessionID = UUID()

    private func configure() throws {
        guard !configured else { return }
        try Wearables.configure()
        configured = true
        selector = AutoDeviceSelector(wearables: Wearables.shared)
    }

    func register() async throws -> String {
        try configure()
        if Wearables.shared.registrationState == .registered {
            return "Connected to Meta AI. Allow glasses camera access next."
        }
        try await Wearables.shared.startRegistration()
        return Wearables.shared.registrationState == .registered
            ? "Connected to Meta AI. Allow glasses camera access next."
            : "Complete the connection in Meta AI, then return here."
    }

    func handleCallback(_ url: URL) async throws -> String? {
        guard URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.contains(where: { $0.name == "metaWearablesAction" }) == true else {
            return nil
        }
        try configure()
        _ = try await Wearables.shared.handleUrl(url)
        return Wearables.shared.registrationState == .registered
            ? "Connected to Meta AI." : "Returned from Meta AI. Check connection or permission."
    }

    func requestCamera() async throws -> String {
        try configure()
        let wearables = Wearables.shared
        let existing = try await wearables.checkPermissionStatus(.camera)
        let permission = existing == .granted ? existing : try await wearables.requestPermission(.camera)
        return permission == .granted ? "Glasses camera access granted." : "Glasses camera access denied."
    }

    func openGlassesAppUpdate() async throws -> String {
        try configure()
        try await Wearables.shared.openDATGlassesAppUpdate()
        return "Finish the glasses app update in Meta AI, then return here."
    }

    func start(
        onFrame: @escaping @MainActor (UIImage) -> Void,
        onFailure: @escaping @MainActor (String) -> Void
    ) async throws {
        try configure()
        let wearables = Wearables.shared
        guard try await wearables.checkPermissionStatus(.camera) == .granted else {
            onFailure("Allow glasses camera access before sending the feed.")
            return
        }
        try Task.checkCancellation()
        guard let selector, selector.activeDevice != nil else {
            onFailure("No connected glasses. Open Meta AI and check pairing.")
            return
        }

        let session = try wearables.createSession(deviceSelector: selector)
        self.session = session
        let id = sessionID
        session.errorPublisher.listen { [weak self] error in
            Task { @MainActor in
                guard self?.sessionID == id else { return }
                onFailure(error.localizedDescription)
            }
        }.store(in: listeners)

        let states = session.stateStream()
        observation = Task { [self] in
            var startedCamera = false
            for await state in states {
                guard !Task.isCancelled, sessionID == id else { return }
                switch state {
                case .started where !startedCamera:
                    do {
                        let config = StreamConfiguration(videoCodec: .raw, resolution: .low, frameRate: 7)
                        guard let camera = try session.addCamera(config: config) else {
                            onFailure("Could not open the glasses camera.")
                            return
                        }
                        self.camera = camera
                        let stream = camera.stream
                        stream.videoFramePublisher.listen { [weak self] frame in
                            guard let image = frame.makeUIImage() else { return }
                            Task { @MainActor in
                                guard self?.sessionID == id else { return }
                                onFrame(image)
                            }
                        }.store(in: listeners)
                        stream.errorPublisher.listen { [weak self] error in
                            Task { @MainActor in
                                guard self?.sessionID == id else { return }
                                onFailure(error.localizedDescription)
                            }
                        }.store(in: listeners)
                        startedCamera = true
                        stream.start()
                    } catch {
                        onFailure(error.localizedDescription)
                        return
                    }
                case .stopped:
                    onFailure("Glasses disconnected or ended the session.")
                    return
                default:
                    break
                }
            }
        }
        try session.start()
    }

    func stop() {
        sessionID = UUID()
        observation?.cancel()
        observation = nil
        listeners = ListenerTokenBag()
        session?.stop()
        camera = nil
        session = nil
    }
}
