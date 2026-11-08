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

  var hubAddress: String {
    didSet { UserDefaults.standard.set(hubAddress, forKey: Self.addressKey) }
  }
  private(set) var hubEnabled = false
  private(set) var hubState: HubClient.State = .disconnected
  private(set) var hubStatus: HubStatus?
  private(set) var lastStatusAt: Date?
  private(set) var preview: UIImage?
  private(set) var stats = FrameForwarder.Stats()
  var hubError: String?

  @ObservationIgnored private let hub: HubClient
  @ObservationIgnored private let forwarder: FrameForwarder
  @ObservationIgnored private var tasks: [Task<Void, Never>] = []
  @ObservationIgnored private var lastAnnouncedHealthy: Bool?

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
        while !Task.isCancelled {
          try? await Task.sleep(for: .seconds(1))
          guard let self else { return }
          self.stats = forwarder.stats
        }
      })
  }

  isolated deinit {
    tasks.forEach { $0.cancel() }
  }

  // MARK: Hub

  func setHubEnabled(_ enabled: Bool) {
    guard enabled else {
      hubEnabled = false
      hubStatus = nil
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
      announceHealthChange(status)
    }
  }

  private func streamChanged(_ stream: String) {
    // Keep the screen awake while streaming; iOS suspends the app (and the
    // glasses stream) when it is backgrounded or locked.
    UIApplication.shared.isIdleTimerDisabled = stream == "streaming"
    Task { await hub.reportPhoneStatus(stream: stream) }
  }

  /// Speaks a VoiceOver announcement when the system starts or stops vouching for itself.
  private func announceHealthChange(_ status: HubStatus) {
    guard status.healthy != lastAnnouncedHealthy else { return }
    lastAnnouncedHealthy = status.healthy
    let message =
      status.healthy
      ? "Belt alerts active."
      : "Warning: alerts not reliable. \(status.reason ?? "")"
    UIAccessibility.post(notification: .announcement, argument: message)
  }
}
