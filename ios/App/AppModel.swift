//
// AppModel.swift
//
// Wires the pieces together:
//   BridgeModel (glasses or test pattern) -> FrameForwarder -> HubClient (WebSocket)
//   HubSession (status, control) -> WearerStatus -> screen, VoiceOver, phone haptics
//

import SwiftUI
import UIKit
import os

@MainActor
final class AppModel: ObservableObject {
    struct Diagnostics: Equatable {
        var framesInPerSecond: Double = 0
        var framesSentPerSecond: Double = 0
        var dropped = 0
        var failed = 0
    }

    let bridge = BridgeModel()
    let hub = HubSession()

    @Published private(set) var condition: WearerCondition
    @Published private(set) var pairing: HubPairing?
    @Published private(set) var diagnostics = Diagnostics()
    /// Recent announcements, newest first, shown to the helper so a demo can follow along.
    @Published private(set) var recentAnnouncements: [String] = []
    var lastSpoken: String? { recentAnnouncements.first }
    /// The control code works for this run but couldn't be saved for the next launch.
    @Published private(set) var codeNotSaved = false
    /// Source for wearer sessions. Test pattern is for bench tests and is always shown as a warning.
    @Published var source: FrameSource {
        didSet { UserDefaults.standard.set(source.rawValue, forKey: Keys.source) }
    }
    /// "Person ahead" speech. Off by default.
    @Published var speakObstacleKinds: Bool {
        didSet { UserDefaults.standard.set(speakObstacleKinds, forKey: Keys.speak) }
    }
    @Published var phoneHaptics: Bool {
        didSet { UserDefaults.standard.set(phoneHaptics, forKey: Keys.haptics) }
    }

    private enum Keys {
        static let source = "wearerSource"
        static let speak = "speakObstacleKinds"
        static let haptics = "phoneHaptics"
    }

    private enum PhoneHaptic { case success, warning, tap }

    private let forwarder: FrameForwarder
    /// Every announcement is also logged (category "announce"), so a test run
    /// can check them with `log stream` without VoiceOver.
    private let log = Logger(subsystem: "com.hackutd26.CameraBridge", category: "announce")
    private let epoch = ContinuousClock.now
    private var gate: AnnouncementGate
    private var speechGate = SectorSpeechGate()
    private var ticker: Task<Void, Never>?
    private var lastRecord: Bool?
    private var resumeAfterStart = false
    private var lastSample: (at: ContinuousClock.Instant, received: Int, stats: FrameForwarder.Stats)?
    /// The condition whose announcement the wearer last heard, and the one held by the gate.
    /// "Recovered" / "Resumed" are worded against what was heard, not against a
    /// short-lived state the rate limit swallowed.
    private var spokenCondition: WearerCondition?
    private var heldCondition: WearerCondition?
    private var firstFrameAt: ContinuousClock.Instant?
    /// After the first frame, keep saying "Starting" this long while the hub warms up.
    private static let warmUp: Duration = .seconds(2)

    init() {
        let defaults = UserDefaults.standard
        source = defaults.string(forKey: Keys.source).flatMap(FrameSource.init(rawValue:)) ?? .glasses
        speakObstacleKinds = defaults.bool(forKey: Keys.speak)
        phoneHaptics = defaults.object(forKey: Keys.haptics) as? Bool ?? true

        let saved = PairingStore.load()
        let pairing = saved.address.isEmpty ? nil : try? HubPairing.validate(address: saved.address, code: saved.code).get()
        self.pairing = pairing
        condition = WearerStatus.condition(WearerInputs(paired: pairing != nil))
        gate = AnnouncementGate(initialKey: WearerKind.off.key)
        forwarder = FrameForwarder(hub: hub.client)

        let forwarder = forwarder
        let client = hub.client
        bridge.onFrame = { image in forwarder.submit(image) }
        bridge.onStreamState = { state, frameSource in
            Task { await client.reportPhoneStatus(stream: state, frameSource: frameSource) }
        }
        if let pairing { hub.connect(pairing) }

        ticker = Task { [weak self] in
            while !Task.isCancelled {
                self?.update()
                try? await Task.sleep(for: .milliseconds(250))
            }
        }
    }

    var isRunning: Bool { bridge.isSending && bridge.activeDestination == .hub }

    /// A banner for the wearer screen while frames are simulated, whatever the state sentence says.
    var simulationBanner: String? {
        guard isRunning else { return nil }
        if bridge.activeSource == .test { return "TEST PATTERN (simulated): not the glasses camera" }
        if bridge.usingMockGlasses { return "MOCK GLASSES (simulated): not the Ray-Bans" }
        return nil
    }
    var isPaused: Bool { hub.status?.isPaused ?? false }

    // MARK: Wearer actions

    func toggleAlerts() {
        isRunning ? stopAlerts() : startAlerts()
    }

    func startAlerts() {
        play(.tap)
        guard pairing != nil else {
            say("setup:none", "Hub not set up. A helper can pair it in Setup.", .warning)
            return
        }
        guard !bridge.isSending else {
            say("setup:busy", "An Advanced diagnostic is using the camera. Stop it in Setup first.", .warning)
            return
        }
        bridge.start(source, destination: .hub)
        guard isRunning else { return }
        // Start means alerts on: undo an earlier Stop or Pause once the hub reports it.
        resumeAfterStart = hub.hasControlCode
        update()
    }

    /// Stop pauses the hub first (explicit, announced, belt off) when the control
    /// code allows it. Without it the hub sees frames stop and plays the fault pattern.
    func stopAlerts() {
        play(.tap)
        resumeAfterStart = false
        if hub.hasControlCode, hub.link == .connected, !isPaused { hub.send(.pause) }
        bridge.stop()
        update()
    }

    func togglePause() {
        play(.tap)
        guard hub.hasControlCode else {
            say("control:nocode", "Pause needs the hub control code. A helper can add it in Setup.", .warning)
            return
        }
        let action: ControlAction = isPaused ? .resume : .pause
        let verb = isPaused ? "resume" : "pause"
        hub.send(action) { [weak self] result in
            guard result != .ok else { return }
            self?.say("control:\(verb):fail", "Could not \(verb). \(result.message)", .warning)
        }
    }

    /// Called when the app leaves the foreground. iOS would suspend the stream anyway;
    /// this is not a deliberate stop, so the hub is left to show its fault pattern.
    func appWentToBackground() {
        if isRunning {
            bridge.fail("Stopped when the app went into the background.")
        } else if bridge.isSending {
            bridge.stop("Stopped when the app went into the background.")
        }
    }

    // MARK: Helper actions

    /// Validates and saves the pairing, then connects. Returns an error message, or nil.
    func savePairing(address: String, code: String) -> String? {
        switch HubPairing.validate(address: address, code: code) {
        case .failure(let problem):
            return problem.localizedDescription
        case .success(let pairing):
            codeNotSaved = !PairingStore.save(address: address, code: code)
            self.pairing = pairing
            hub.connect(pairing)
            return nil
        }
    }

    func forgetPairing() {
        if isRunning { bridge.stop() }
        PairingStore.clear()
        codeNotSaved = false
        pairing = nil
        hub.disconnect()
    }

    func setClassifier(_ classifier: Classifier) {
        hub.send(.set(SettingsChange(classifier: classifier)))
    }

    func setAlertRange(_ range: AlertRange) {
        hub.send(.set(SettingsChange(alertRange: range)))
    }

    func beltTest() {
        hub.send(.beltTest)
    }

    /// Only call after the helper confirmed consent in the UI.
    func setRecording(_ on: Bool) {
        hub.send(.set(SettingsChange(record: on)))
    }

    /// `get` changes nothing but proves the code works and fetches the settings.
    func verifyControlCode() {
        hub.send(.get)
    }

    var canVerifyControlCode: Bool {
        hub.hasControlCode && hub.link == .connected
    }

    // MARK: Updates

    private var now: TimeInterval {
        let elapsed = epoch.duration(to: .now).components
        return Double(elapsed.seconds) + Double(elapsed.attoseconds) / 1e18
    }

    private func inputs() -> WearerInputs {
        var input = WearerInputs()
        input.paired = pairing != nil
        input.source = bridge.activeSource ?? source
        input.mockGlasses = bridge.usingMockGlasses
        if !bridge.isSending || bridge.lastFrame == nil { firstFrameAt = nil }
        if bridge.isSending {
            if bridge.activeDestination != .hub {
                input.session = .diagnostic
            } else if bridge.lastFrame == nil {
                input.session = .starting
            } else {
                // The hub's status lags the first frame ("waiting for camera frames").
                // That warm-up isn't a fault, so it stays "Starting" briefly.
                let first = firstFrameAt ?? .now
                firstFrameAt = first
                let warmingUp = first.duration(to: .now) < Self.warmUp && hub.status?.healthy != true
                input.session = warmingUp ? .starting : .running
            }
        } else if let failure = bridge.failure, bridge.failedDestination == .hub {
            input.session = .stopped(reason: failure)
        }
        input.link = hub.link
        input.status = hub.status
        input.statusAge = hub.statusAge()
        input.cameraFrameAge = bridge.lastFrame.map { Self.seconds($0.duration(to: .now)) }
        return input
    }

    private func update() {
        let input = inputs()
        let new = WearerStatus.condition(input)
        if new != condition {
            let heard = spokenCondition ?? condition
            condition = new
            if let text = WearerStatus.announcement(from: heard, to: new) {
                if let out = gate.submit(Announcement(key: new.kind.key, text: text), now: now) {
                    spokenCondition = new
                    post(out, haptic(for: new.tone))
                } else {
                    heldCondition = gate.pending == nil ? nil : new
                }
            } else {
                // Back to what the wearer last heard: drop anything held back.
                gate.dropPending()
                heldCondition = nil
            }
        }
        if let held = gate.poll(now: now) {
            spokenCondition = heldCondition ?? condition
            heldCondition = nil
            post(held, haptic(for: condition.tone))
        }

        if UIApplication.shared.isIdleTimerDisabled != isRunning {
            // Locking the phone would background the app and stop the stream.
            UIApplication.shared.isIdleTimerDisabled = isRunning
        }
        if resumeAfterStart, isRunning, let status = hub.status {
            resumeAfterStart = false
            if status.isPaused { hub.send(.resume) }
        }
        if let record = hub.settings?.record {
            if let lastRecord, lastRecord != record {
                say("record:\(record)", record ? "Hub recording on." : "Hub recording off.", .tap)
            }
            lastRecord = record
        }
        speakObstacleKind(input)
        sampleDiagnostics()
    }

    /// Optional "Person ahead" speech, only while the hub is vouching for fresh data.
    private func speakObstacleKind(_ input: WearerInputs) {
        guard speakObstacleKinds, let status = input.status else { return }
        switch condition.kind {
        case .alertsOn, .simulated: break
        default: return
        }
        guard let phrase = SectorSpeech.phrase(for: status), let said = speechGate.submit(phrase, now: now) else { return }
        announce(said.text, important: false)
    }

    private func sampleDiagnostics() {
        let at = ContinuousClock.now
        let stats = forwarder.stats
        let received = bridge.framesReceived
        guard let last = lastSample else {
            lastSample = (at, received, stats)
            return
        }
        let elapsed = Self.seconds(last.at.duration(to: at))
        guard elapsed >= 1 else { return }
        diagnostics = Diagnostics(
            framesInPerSecond: Double(received - last.received) / elapsed,
            framesSentPerSecond: Double(stats.sent - last.stats.sent) / elapsed,
            dropped: stats.dropped,
            failed: stats.failed)
        lastSample = (at, received, stats)
    }

    // MARK: Speech and haptics

    private func say(_ key: String, _ text: String, _ haptic: PhoneHaptic) {
        if let out = gate.submit(Announcement(key: key, text: text), now: now) { post(out, haptic) }
    }

    private func post(_ announcement: Announcement, _ haptic: PhoneHaptic) {
        speechGate.noteStateAnnouncement(now: now)
        announce(announcement.text, important: haptic == .warning)
        play(haptic)
    }

    private func announce(_ text: String, important: Bool) {
        recentAnnouncements.insert(text, at: 0)
        if recentAnnouncements.count > 6 { recentAnnouncements.removeLast() }
        log.notice("announce: \(text, privacy: .public)")
        var message = AttributedString(text)
        message.accessibilitySpeechAnnouncementPriority = important ? .high : .default
        AccessibilityNotification.Announcement(message).post()
    }

    private func haptic(for tone: WearerTone) -> PhoneHaptic {
        switch tone {
        case .ok: return .success
        case .warning: return .warning
        case .off: return .tap
        }
    }

    private func play(_ haptic: PhoneHaptic) {
        guard phoneHaptics else { return }
        switch haptic {
        case .success: UINotificationFeedbackGenerator().notificationOccurred(.success)
        case .warning: UINotificationFeedbackGenerator().notificationOccurred(.warning)
        case .tap: UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        }
    }

    private static func seconds(_ duration: Duration) -> TimeInterval {
        let c = duration.components
        return Double(c.seconds) + Double(c.attoseconds) / 1e18
    }
}
