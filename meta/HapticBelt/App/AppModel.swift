//
// AppModel.swift
//
// Wires the three pieces of the phone app together:
//   GlassesController (Meta SDK)  ->  FrameForwarder  ->  HubClient (WebSocket)
// and exposes their state to the UI. No SDK or socket details live here.
//

import MWDATCore
import Observation
import UIKit

@Observable
@MainActor
final class AppModel {
  static let defaultHubAddress = "ws://192.168.1.100:8765"
  private static let addressKey = "hubAddress"

  let glasses: GlassesController
  let phoneHaptics = PhoneHaptics()
  let speechOutput = SpeechOutput()
  let belt = BeltClient()
  private(set) var beltAlertsEnabled = false
  private(set) var beltRelayStatus = "Belt alerts off"
  var beltTestMotor = 2
  private(set) var speechEnabled = false
  var detailedSpeech = true
  var speechReminders = true
  var patternSample: AlertPatternSample = .obstacle
  var phoneStrength = 0.7
  private(set) var patternTestStatus = "Pattern tests turn automatic voice and vibration off."
  private(set) var speechStatus = "Spoken alerts off"

  private(set) var phoneVibrationEnabled = false
  private(set) var phoneVibrationStatus = "Phone vibrations off"

  var hubAddress: String {
    didSet { UserDefaults.standard.set(hubAddress, forKey: Self.addressKey) }
  }
  private(set) var hubEnabled = false
  private(set) var hubState: HubClient.State = .disconnected
  private(set) var hubStatus: HubStatus?
  private(set) var lastStatusAt: Date?
  private(set) var preview: UIImage?
  private(set) var stats = FrameForwarder.Stats()
  private(set) var cameraFps = 0.0
  private(set) var sentFps = 0.0
  private(set) var encodeMs = 0.0
  private(set) var sendMs = 0.0
  var hubError: String?

  @ObservationIgnored private let hub: HubClient
  @ObservationIgnored private let forwarder: FrameForwarder
  @ObservationIgnored private var tasks: [Task<Void, Never>] = []
  @ObservationIgnored private var lastAnnouncedHealthy: Bool?
  @ObservationIgnored private var lastStatusUptime: Double?
  @ObservationIgnored private var phonePolicy = PhoneHapticPolicy()
  @ObservationIgnored private var appActive = false
  @ObservationIgnored private var speechPolicy = SpeechPolicy()
  @ObservationIgnored private var beltTestUntil = 0.0
  @ObservationIgnored private var beltConnectionFault: String?

  init(wearables: WearablesInterface) {
    let hub = HubClient()
    let forwarder = FrameForwarder(hub: hub)
    let (previews, previewSink) = AsyncStream.makeStream(of: UIImage.self, bufferingPolicy: .bufferingNewest(1))

    self.hub = hub
    self.forwarder = forwarder
    self.hubAddress = UserDefaults.standard.string(forKey: Self.addressKey) ?? Self.defaultHubAddress
    self.glasses = GlassesController(wearables: wearables) { image in
      forwarder.submit(image)
      previewSink.yield(image)
    }

    glasses.onStreamStateChange = { [weak self] stream in self?.streamChanged(stream) }
    belt.onFailure = { [weak self] reason in
      guard let self else { return }
      if self.beltAlertsEnabled { self.beltConnectionFault = "Belt delivery unavailable. Belt alerts stopped." }
      self.beltAlertsEnabled = false
      self.beltTestUntil = 0
      self.beltRelayStatus = reason
      self.updatePhoneVibration()
    }

    tasks.append(Task { [weak self] in
      while !Task.isCancelled {
        do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
        self?.updatePhoneVibration()
      }
    })

    tasks.append(
      Task { [weak self] in
        for await event in hub.events { self?.handle(event) }
      })
    tasks.append(
      Task { [weak self] in
        for await image in previews { self?.preview = image }
      })
    tasks.append(
      Task { [weak self] in
        var sampledAt = ContinuousClock.now
        var previous = forwarder.stats
        while !Task.isCancelled {
          try? await Task.sleep(for: .seconds(1))
          guard let self else { return }
          let now = ContinuousClock.now
          let duration = sampledAt.duration(to: now).components
          let seconds = max(0.001, Double(duration.seconds) + Double(duration.attoseconds) / 1e18)
          let current = forwarder.stats
          self.cameraFps = Double(current.received - previous.received) / seconds
          self.sentFps = Double(current.sent - previous.sent) / seconds
          let completed = current.completed - previous.completed
          self.encodeMs = completed > 0 ? (current.encodeTotalMs - previous.encodeTotalMs) / Double(completed) : 0
          self.sendMs = completed > 0 ? (current.sendTotalMs - previous.sendTotalMs) / Double(completed) : 0
          self.speechOutput.refreshRoute()
          self.stats = current
          previous = current
          sampledAt = now
        }
      })
  }

  isolated deinit {
    tasks.forEach { $0.cancel() }
  }

  // MARK: Hub

  func setPhoneVibrationEnabled(_ enabled: Bool) {
    phoneVibrationEnabled = enabled
    if !enabled { phoneHaptics.stop() }
    updatePhoneVibration()
  }

  func setAppActive(_ active: Bool) {
    appActive = active
    if active { speechOutput.refreshRoute() }
    if !active {
      setBeltAlertsEnabled(false)
      belt.disconnect()
      phoneHaptics.stop()
      speechOutput.stop()
      // Never resume obstacle pulses from status received before backgrounding.
      lastStatusUptime = nil
    }
    updatePhoneVibration()
  }

  func testPhoneVibration() {
    guard appActive else { return }
    phoneVibrationEnabled = false
    updatePhoneVibration()
    phoneHaptics.play(.obstacle(level: 200), strength: phoneStrength)
    phoneVibrationStatus = "Test buzz requested — confirm you can feel it"
  }

  func stopCamera() {
    setBeltAlertsEnabled(false)
    setPhoneVibrationEnabled(false)
    setSpeechEnabled(false)
    glasses.stop()
  }

  func setSpeechEnabled(_ enabled: Bool) {
    speechEnabled = enabled
    if enabled { _ = speechOutput.prepare() } else { speechOutput.stop() }
    updatePhoneVibration()
  }

  func testGlassesSpeech() {
    setSpeechEnabled(false)
    speechOutput.speak("Glasses audio test. This is a test, not an obstacle alert.", active: appActive)
  }

  func testAlertPattern() {
    guard appActive else {
      patternTestStatus = "Open the app to play a pattern test."
      return
    }
    setPhoneVibrationEnabled(false)
    setSpeechEnabled(false)
    phoneHaptics.play(patternSample.cue, strength: phoneStrength)
    speechOutput.speak(patternSample.explanation, active: appActive)
    patternTestStatus = "Test: \(patternSample.title). Automatic alerts are off."
  }

  func repeatCurrentAlert() {
    guard appActive, speechEnabled else { return }
    speechPolicy.requestRepeat()
    updatePhoneVibration()
  }

  func scanForBelt() {
    guard appActive else { return }
    beltConnectionFault = nil
    belt.scan()
  }

  func setBeltAlertsEnabled(_ enabled: Bool) {
    beltConnectionFault = nil
    beltTestUntil = 0
    beltAlertsEnabled = enabled && belt.canRelay && appActive
    if !beltAlertsEnabled { belt.submit(.off, armed: false) }
    updatePhoneVibration()
  }

  func disconnectBelt() {
    setBeltAlertsEnabled(false)
    belt.disconnect()
  }

  func testBeltMotor() {
    guard appActive, belt.canRelay else { return }
    setBeltAlertsEnabled(false)
    beltTestUntil = ProcessInfo.processInfo.systemUptime + 0.2
    belt.testMotor(beltTestMotor)
    beltRelayStatus = "Motor test requested; belt alerts are off"
  }

  private func updatePhoneVibration() {
    let now = ProcessInfo.processInfo.systemUptime
    let hubAssessment = AlertAssessment.evaluate(
      connected: hubEnabled && hubState == .connected, streaming: glasses.isStreaming,
      status: hubStatus, receivedAt: lastStatusUptime, now: now)
    let assessment = beltConnectionFault.map(AlertAssessment.fault) ?? hubAssessment
    if now >= beltTestUntil {
      let relay = BeltRelayPolicy.evaluate(enabled: beltAlertsEnabled, active: appActive,
        connected: hubEnabled && hubState == .connected, streaming: glasses.isStreaming,
        status: hubStatus, receivedAt: lastStatusUptime, now: now)
      belt.submit(relay.command, armed: beltAlertsEnabled)
      beltRelayStatus = beltConnectionFault ?? relay.label
    }
    let output = phonePolicy.step(assessment: assessment, enabled: phoneVibrationEnabled,
                                  active: appActive, now: now)
    phoneVibrationStatus = output.label
    if output.cancel { phoneHaptics.cancelPattern() }
    if let cue = output.cue { phoneHaptics.play(cue, strength: phoneStrength) }
    // Avoid competing with VoiceOver. Manual audio test remains user initiated.
    let canSpeak = speechEnabled && speechOutput.bluetoothReady && !speechOutput.interrupted
      && !UIAccessibility.isVoiceOverRunning
    let spoken = speechPolicy.step(assessment, enabled: canSpeak, active: appActive,
                                   detailed: detailedSpeech, now: now, reminders: speechReminders)
    if spoken.cancel { speechOutput.stop() }
    if let phrase = spoken.phrase { speechOutput.speak(phrase) }
    if !speechEnabled { speechStatus = "Spoken alerts off" }
    else if !appActive { speechStatus = "Paused while app is inactive" }
    else if UIAccessibility.isVoiceOverRunning { speechStatus = "Automatic speech paused for VoiceOver" }
    else if !speechOutput.bluetoothReady { speechStatus = "Select glasses as the Bluetooth audio output" }
    else if speechOutput.interrupted { speechStatus = "Speech interrupted" }
    else { speechStatus = assessment.label }
  }

  func setHubEnabled(_ enabled: Bool) {
    guard enabled else {
      hubEnabled = false
      hubStatus = nil
      updatePhoneVibration()
      Task { await hub.disconnect() }
      return
    }
    guard let url = HubAddress.parse(hubAddress) else {
      hubError = "Enter the address the hub prints, e.g. ws://192.168.1.20:8765"
      return
    }
    hubEnabled = true
    Task { await hub.connect(to: url) }
  }

  /// The status is only meaningful while it keeps arriving.
  func isStatusFresh(now: Date = .now) -> Bool {
    guard let lastStatusAt else { return false }
    return now.timeIntervalSince(lastStatusAt) < 2
  }

  // MARK: Private

  private func handle(_ event: HubClient.Event) {
    switch event {
    case .state(let state):
      hubState = state
      if state != .connected { hubStatus = nil }
    case .status(let status):
      hubStatus = status
      lastStatusAt = .now
      lastStatusUptime = ProcessInfo.processInfo.systemUptime
      announceHealthChange(status)
    }
    updatePhoneVibration()
  }

  private func streamChanged(_ stream: String) {
    // Keep the screen awake while streaming; iOS suspends the app (and the
    // glasses stream) when it is backgrounded or locked.
    UIApplication.shared.isIdleTimerDisabled = stream == "streaming"
    updatePhoneVibration()
    Task { await hub.reportPhoneStatus(stream: stream) }
  }

  /// Speaks a VoiceOver announcement when the system starts or stops vouching for itself.
  private func announceHealthChange(_ status: HubStatus) {
    guard status.healthy != lastAnnouncedHealthy else { return }
    lastAnnouncedHealthy = status.healthy
    let message =
      status.healthy
      ? "Hub perception available."
      : "Warning: alerts not reliable. \(status.reason ?? "")"
    if UIAccessibility.isVoiceOverRunning {
      UIAccessibility.post(notification: .announcement, argument: message)
    }
  }
}
