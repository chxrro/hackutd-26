//
// AppModel.swift
//
// Wires the pieces together:
//   BridgeModel (glasses or test pattern) -> FrameForwarder -> HubClient (WebSocket)
//   HubSession (status, control) -> WearerStatus -> screen, VoiceOver, phone haptics
//

import AVFAudio
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
    @Published private(set) var locationSummary: String?
    @Published private(set) var locating = false
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
    private enum Speech { case status, warning, obstacle, interruptibleObstacle, location, hearStatus, practice }

    private let forwarder: FrameForwarder
    /// Every announcement is also logged (category "announce"), so a test run
    /// can check them with `log stream` without VoiceOver.
    private let log = Logger(subsystem: "com.hackutd26.CameraBridge", category: "announce")
    private let epoch = ContinuousClock.now
    private var announcer: WearerAnnouncer
    private var speechGate = SectorSpeechGate()
    private let obstacleSpeaker: AVSpeechSynthesizer = {
        let speaker = AVSpeechSynthesizer()
        speaker.usesApplicationAudioSession = false
        return speaker
    }()
    private let locationLookup = LocationLookup()
    private lazy var manualSpeaker: AVSpeechSynthesizer = {
        let speaker = AVSpeechSynthesizer()
        speaker.usesApplicationAudioSession = false
        return speaker
    }()
    private var pendingLocationSpeech: (text: String, expires: TimeInterval)?
    private var locationSummaryExpires: TimeInterval?
    private var locationVoiceOverUntil: TimeInterval?
    private var pendingStatusUntil: TimeInterval?
    private var manualSpeechIsLocation = false
    private var ticker: Task<Void, Never>?
    private var lastRecord: Bool?
    private var resumeAfterStart = false
    private var lastSample: (at: ContinuousClock.Instant, received: Int, stats: FrameForwarder.Stats)?
    private var firstFrameAt: ContinuousClock.Instant?
    /// When the hub's status first said "resuming alerts" without a break.
    private var resumingSince: ContinuousClock.Instant?
    /// The wearer's last Stop, until the next Start: whether the hub confirmed it,
    /// when it was requested and when the hub acknowledged it (to read later statuses against).
    private var wearerStop: WearerStop?
    private var stopRequestedAt: Date?
    private var stopAckedAt: Date?
    private var stopAttempt = 0
    /// After the first frame, keep saying "Starting" this long while the hub warms up.
    private static let warmUp: Duration = .seconds(2)
    // Spoken estimates expire after three expected status intervals (~5 Hz).
    private static let obstacleStatusTimeout: TimeInterval = 0.6

    init() {
        let defaults = UserDefaults.standard
        source = defaults.string(forKey: Keys.source).flatMap(FrameSource.init(rawValue:)) ?? .glasses
        speakObstacleKinds = defaults.bool(forKey: Keys.speak)
        phoneHaptics = defaults.object(forKey: Keys.haptics) as? Bool ?? true

        let saved = PairingStore.load()
        let pairing = saved.address.isEmpty ? nil : try? HubPairing.validate(address: saved.address, code: saved.code).get()
        self.pairing = pairing
        let initial = WearerStatus.condition(WearerInputs(paired: pairing != nil))
        condition = initial
        announcer = WearerAnnouncer(initial: initial)
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
        stopManualSpeech()
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
        // The hub ends its stop on our "starting" phone_status (or the first frame).
        clearWearerStop()
        // Start means alerts on: undo an earlier Stop or Pause once the hub reports it.
        resumeAfterStart = hub.hasControlCode
        update()
    }

    /// Stop tells the hub first (`end_session`, docs/protocol.md "Deliberate stop vs.
    /// lost phone"), then stops the camera. A hub that accepts it plays the
    /// session-ended cue and stays quiet. Without the control code or a link, or if
    /// the hub refuses or doesn't answer, it sees frames stop like a lost phone and
    /// plays the fault pattern; the wearer is told which. HubClient holds frames
    /// from the moment `end_session` is sent, so none can follow it.
    func stopAlerts() {
        play(.tap)
        resumeAfterStart = false
        stopAttempt += 1
        let attempt = stopAttempt
        stopRequestedAt = .now
        stopAckedAt = nil
        if hub.hasControlCode, hub.link == .connected {
            wearerStop = .sending
            hub.send(.endSession) { [weak self] result in
                guard let self, self.stopAttempt == attempt, self.wearerStop != nil else { return }
                if result == .ok {
                    self.wearerStop = .confirmed
                    self.stopAckedAt = .now
                } else {
                    self.wearerStop = .notConfirmed(result.message)
                }
                self.update()
            }
        } else {
            wearerStop = .notConfirmed(hub.hasControlCode ? "The hub isn’t connected." : "No hub control code.")
        }
        bridge.stop()
        update()
    }

    private func clearWearerStop() {
        stopAttempt += 1
        wearerStop = nil
        stopRequestedAt = nil
        stopAckedAt = nil
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
    /// this is not a deliberate stop: no `end_session`, so the hub plays its fault pattern.
    func appWentToBackground() {
        stopObstacleSpeech()
        stopManualSpeech()
        pendingStatusUntil = nil
        locationLookup.cancel()
        pendingLocationSpeech = nil
        locationSummaryExpires = nil
        locationVoiceOverUntil = nil
        locationSummary = nil
        locating = false
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
        clearWearerStop()
        PairingStore.clear()
        codeNotSaved = false
        pairing = nil
        hub.disconnect()
    }

    func reconnectHub() {
        if let pairing { hub.connect(pairing) }
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
        input.wearerStop = wearerStop
        input.hubStopped = hubStoppedSinceStop()
        if let status = hub.status, !status.isPaused, status.reason == WearerStatus.resumingReason {
            let since = resumingSince ?? .now
            resumingSince = since
            input.resumingFor = Self.seconds(since.duration(to: .now))
        } else {
            resumingSince = nil
        }
        return input
    }

    /// The hub's `stopped` flag from a status that arrived after the Stop request
    /// (counts when true) or after the hub acknowledged it (counts when false: an
    /// earlier status may predate the stop).
    private func hubStoppedSinceStop() -> Bool? {
        guard wearerStop != nil, let requested = stopRequestedAt, let status = hub.status,
            let at = hub.lastStatusAt, let stopped = status.stopped
        else { return nil }
        if stopped { return at > requested ? true : nil }
        if let acked = stopAckedAt, at > acked { return false }
        return nil
    }

    private func update() {
        let input = inputs()
        let new = WearerStatus.condition(input)
        if new != condition { condition = new }
        if let out = announcer.update(new, now: now) {
            post(out.announcement, haptic(for: out.tone))
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
        if let expires = pendingStatusUntil {
            if now >= expires {
                pendingStatusUntil = nil
            } else if !obstacleSpeaker.isSpeaking {
                readBeltStatus()
            }
        }
        speakPendingLocation(input)
        if let expires = locationSummaryExpires, now >= expires {
            locationSummary = nil
            locationSummaryExpires = nil
        }
        sampleDiagnostics()
    }

    /// Optional "Person ahead" speech, only while the hub is vouching for fresh data.
    private func speakObstacleKind(_ input: WearerInputs) {
        let prefix: String
        switch condition.kind {
        case .alertsOn: prefix = ""
        case .simulated: prefix = "Simulation. "
        default:
            stopObstacleSpeech()
            return
        }
        guard speakObstacleKinds, let age = input.statusAge, age <= Self.obstacleStatusTimeout,
            let status = input.status, let phrase = SectorSpeech.phrase(for: status) else {
            stopObstacleSpeech()
            return
        }
        if UIAccessibility.isVoiceOverRunning { stopObstacleSpeech() }
        guard (phrase.urgent || !obstacleSpeaker.isSpeaking),
            let said = speechGate.submit(phrase, now: now) else { return }
        if said.urgent { stopObstacleSpeech() }
        stopManualSpeech()
        let interruptLocation = locationVoiceOverUntil.map { now < $0 } ?? false
        locationVoiceOverUntil = nil
        announce(prefix + said.text, as: said.urgent || interruptLocation ? .interruptibleObstacle : .obstacle)
    }

    func findLocation() {
        guard !locating else { return }
        play(.tap)
        locating = true
        locationSummary = nil
        locationSummaryExpires = nil
        pendingLocationSpeech = nil
        stopManualSpeech()
        locationLookup.start(allowPermissionPrompt: !isRunning) { [weak self] text in
            guard let self else { return }
            locating = false
            locationSummary = text
            locationSummaryExpires = now + 30
            pendingLocationSpeech = (text, now + 10)
            update()
        }
    }

    private func speakPendingLocation(_ input: WearerInputs) {
        guard WearerStatus.permitsLocationSpeech(input, condition: condition,
                                                statusTimeout: Self.obstacleStatusTimeout) else {
            if manualSpeechIsLocation {
                stopManualSpeech()
            }
            return
        }
        guard let pending = pendingLocationSpeech else { return }
        if now >= pending.expires {
            pendingLocationSpeech = nil
            return
        }
        guard !obstacleSpeaker.isSpeaking, !manualSpeaker.isSpeaking,
              speechGate.lastAt.map({ now - $0 >= speechGate.minInterval }) ?? true,
              speechGate.stateAt.map({ now - $0 >= speechGate.quietAfterState }) ?? true else { return }
        pendingLocationSpeech = nil
        if UIAccessibility.isVoiceOverRunning { locationVoiceOverUntil = now + 10 }
        announce(pending.text, as: .location)
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

    func readBeltStatus() {
        pendingLocationSpeech = nil
        stopManualSpeech()
        if obstacleSpeaker.isSpeaking && !UIAccessibility.isVoiceOverRunning {
            pendingStatusUntil = now + 5
        } else {
            pendingStatusUntil = nil
            let current = WearerStatus.condition(inputs())
            announce("\(current.sentence). \(current.detail)", as: .hearStatus)
        }
    }

    func speakPractice(_ text: String) {
        guard !bridge.isSending else { return }
        pendingLocationSpeech = nil
        stopManualSpeech()
        announce(text, as: .practice)
    }

    private func say(_ key: String, _ text: String, _ haptic: PhoneHaptic) {
        if let out = announcer.say(Announcement(key: key, text: text), now: now) { post(out, haptic) }
    }

    private func post(_ announcement: Announcement, _ haptic: PhoneHaptic) {
        speechGate.noteStateAnnouncement(now: now)
        stopManualSpeech()
        locationVoiceOverUntil = nil
        announce(announcement.text, as: haptic == .warning ? .warning : .status)
        play(haptic)
    }

    private func stopObstacleSpeech() {
        if obstacleSpeaker.isSpeaking { obstacleSpeaker.stopSpeaking(at: .immediate) }
    }

    func stopManualSpeech() {
        manualSpeechIsLocation = false
        if manualSpeaker.isSpeaking { manualSpeaker.stopSpeaking(at: .immediate) }
    }

    private func announce(_ text: String, as speech: Speech = .status) {
        if speech != .location {
            recentAnnouncements.insert(text, at: 0)
            if recentAnnouncements.count > 6 { recentAnnouncements.removeLast() }
            log.notice("announce: \(text, privacy: .public)")
        }
        if !UIAccessibility.isVoiceOverRunning {
            if speech == .location || speech == .hearStatus || speech == .practice {
                manualSpeechIsLocation = speech == .location
                manualSpeaker.speak(AVSpeechUtterance(string: text))
                return
            }
            if speech == .obstacle || speech == .interruptibleObstacle {
                obstacleSpeaker.speak(AVSpeechUtterance(string: text))
                return
            }
        }
        var message = AttributedString(text)
        switch speech {
        case .warning: message.accessibilitySpeechAnnouncementPriority = .high
        case .obstacle, .hearStatus: message.accessibilitySpeechAnnouncementPriority = .low
        case .status, .interruptibleObstacle, .location, .practice:
            message.accessibilitySpeechAnnouncementPriority = .default
        }
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
