//
// HubSession.swift
//
// Main-actor view of the hub link for the UI: connection state, the latest
// status and when it arrived, the hub's settings, and control requests with
// their replies. A request the hub never answers (for example a hub that
// predates the control draft) times out and is reported as a failure.
//

import Combine
import Foundation

@MainActor
final class HubSession: ObservableObject {
  enum ControlResult: Equatable {
    case ok
    case rejected(String)
    case noReply
    case notConnected

    var message: String {
      switch self {
      case .ok: return "Done."
      case .rejected(let why): return "The hub refused: \(why)."
      case .noReply: return "No reply from the hub. It may not support settings yet."
      case .notConnected: return "The hub isn’t connected."
      }
    }
  }

  /// Whether the hub accepted our control code, as far as the app has seen.
  enum ControlPairing: Equatable {
    case noCode, unverified, paired, rejected, noReply
  }

  struct Pending: Equatable {
    let action: ControlAction
    let sentAt: Date
  }

  static let replyTimeout: TimeInterval = 3

  @Published private(set) var link: HubClient.State = .disconnected
  @Published private(set) var status: HubStatus?
  @Published private(set) var lastStatusAt: Date?
  @Published private(set) var settings: HubSettings?
  @Published private(set) var controlPairing: ControlPairing = .noCode
  @Published private(set) var pending: [Int: Pending] = [:]
  /// The outcome of the most recent request, for the helper screen.
  @Published private(set) var lastResult: (action: ControlAction, result: ControlResult)?

  let client: HubClient
  private(set) var pairing: HubPairing?
  private var nextID = 0
  private var completions: [Int: (ControlResult) -> Void] = [:]
  private var eventTask: Task<Void, Never>?

  init(client: HubClient = HubClient()) {
    self.client = client
    eventTask = Task { [weak self] in
      for await event in client.events {
        guard let self else { return }
        self.handle(event)
      }
    }
  }

  var hasControlCode: Bool { pairing?.controlCode != nil }

  func connect(_ pairing: HubPairing) {
    self.pairing = pairing
    controlPairing = pairing.controlCode == nil ? .noCode : .unverified
    status = nil
    lastStatusAt = nil
    settings = nil
    Task { await client.connect(to: pairing.url, controlCode: pairing.controlCode) }
  }

  func disconnect() {
    pairing = nil
    controlPairing = .noCode
    failAllPending(.notConnected)
    Task { await client.disconnect() }
  }

  /// Seconds since the last status, or nil if none arrived on this connection.
  func statusAge(now: Date = .now) -> TimeInterval? {
    lastStatusAt.map { now.timeIntervalSince($0) }
  }

  func isPending(_ matches: (ControlAction) -> Bool) -> Bool {
    pending.values.contains { matches($0.action) }
  }

  /// Sends a control request; `completion` gets the hub's answer or a timeout.
  func send(_ action: ControlAction, completion: ((ControlResult) -> Void)? = nil) {
    guard link == .connected else {
      finish(nil, action, .notConnected, completion)
      return
    }
    nextID += 1
    let id = nextID
    pending[id] = Pending(action: action, sentAt: .now)
    if let completion { completions[id] = completion }
    Task {
      let sent = await client.sendControl(id: id, action)
      if !sent { resolve(id, .notConnected) }
    }
    Task {
      try? await Task.sleep(for: .seconds(Self.replyTimeout))
      resolve(id, .noReply)
    }
  }

  // MARK: Private

  private func handle(_ event: HubClient.Event) {
    switch event {
    case .state(let state):
      link = state
      if state != .connected {
        status = nil
        lastStatusAt = nil
        failAllPending(.notConnected)
      } else if pairing?.controlCode != nil {
        // Pairing belongs to one connection: HubClient re-sent hello with the
        // code, so check it again and fetch the settings.
        controlPairing = .unverified
        send(.get)
      }
    case .status(let status):
      self.status = status
      lastStatusAt = .now
      if let settings = status.settings { self.settings = settings }
    case .ack(let ack):
      if let settings = ack.settings { self.settings = settings }
      if ack.ok {
        controlPairing = .paired
      } else if ack.error == HubAck.notPaired {
        controlPairing = .rejected
      }
      if let id = ack.id { resolve(id, ack.ok ? .ok : .rejected(ack.error ?? "unknown error")) }
    }
  }

  private func resolve(_ id: Int, _ result: ControlResult) {
    guard let request = pending.removeValue(forKey: id) else { return }
    if result == .noReply, controlPairing == .unverified { controlPairing = .noReply }
    finish(id, request.action, result, completions.removeValue(forKey: id))
  }

  private func finish(_ id: Int?, _ action: ControlAction, _ result: ControlResult, _ completion: ((ControlResult) -> Void)?) {
    lastResult = (action, result)
    completion?(result)
  }

  private func failAllPending(_ result: ControlResult) {
    for id in pending.keys { resolve(id, result) }
  }
}
