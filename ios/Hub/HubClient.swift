//
// HubClient.swift
//
// WebSocket connection to the hub laptop (docs/protocol.md section 1).
// Reconnects automatically while a URL is set, and re-sends `hello` (with the
// control code) and the last `phone_status` (with its `frame_source`) after
// every reconnect, before any frame. Knows nothing about the glasses. Based on meta/HapticBelt/Hub/HubClient.swift.
//

import Foundation

actor HubClient {
  enum State: Equatable, Sendable {
    case disconnected
    case connecting
    case connected
    case failed(String)
  }

  enum Event: Sendable {
    case state(State)
    case status(HubStatus)
    case ack(HubAck)
  }

  /// Connection changes and hub messages, for the UI.
  nonisolated let events: AsyncStream<Event>

  private let continuation: AsyncStream<Event>.Continuation
  private let session: URLSession
  private let retryDelay: Duration
  private var task: URLSessionWebSocketTask?
  private var url: URL?
  private var controlCode: String?
  /// Bumped whenever the socket is replaced, so callbacks from an old socket are ignored.
  private var generation = 0
  /// The last `phone_status` message as sent, re-sent after a reconnect.
  private var lastPhoneStatus: String?
  private(set) var state: State = .disconnected {
    didSet { if state != oldValue { continuation.yield(.state(state)) } }
  }

  init(retryDelay: Duration = .seconds(1)) {
    (events, continuation) = AsyncStream.makeStream(of: Event.self, bufferingPolicy: .bufferingNewest(64))
    let config = URLSessionConfiguration.ephemeral
    config.timeoutIntervalForRequest = 5
    config.allowsCellularAccess = false  // the hub is on the local network
    session = URLSession(configuration: config)
    self.retryDelay = retryDelay
  }

  func connect(to url: URL, controlCode: String?) {
    self.url = url
    self.controlCode = controlCode
    generation += 1
    open(generation)
  }

  func disconnect() {
    url = nil
    generation += 1
    task?.cancel(with: .goingAway, reason: nil)
    task = nil
    state = .disconnected
  }

  /// Sends one binary frame. Returns false if not connected or the send failed.
  func send(_ data: Data) async -> Bool {
    guard state == .connected, let task else { return false }
    let gen = generation
    do {
      try await task.send(.data(data))
      return true
    } catch {
      fail(error, gen)
      return false
    }
  }

  /// Tells the hub what the stream is doing and where its frames come from.
  /// Re-sent after every reconnect.
  func reportPhoneStatus(stream: String, frameSource: HubFrameSource?) async {
    let text = HubOutgoing.phoneStatus(stream: stream, frameSource: frameSource)
    lastPhoneStatus = text
    _ = await sendText(text)
  }

  /// Sends one control request. Returns false if it couldn't be sent. The reply
  /// arrives later as an `.ack` event (or never, from a hub without control).
  func sendControl(id: Int, _ action: ControlAction) async -> Bool {
    await sendText(HubOutgoing.control(id: id, action))
  }

  // MARK: Private

  private func sendText(_ text: String) async -> Bool {
    guard state == .connected, let task else { return false }
    let gen = generation
    do {
      try await task.send(.string(text))
      return true
    } catch {
      fail(error, gen)
      return false
    }
  }

  private func open(_ gen: Int) {
    guard let url, gen == generation else { return }
    task?.cancel(with: .goingAway, reason: nil)
    let task = session.webSocketTask(with: url)
    task.maximumMessageSize = 1 << 20
    self.task = task
    state = .connecting
    task.resume()
    Task { await self.handshake(task, gen) }
  }

  private func handshake(_ task: URLSessionWebSocketTask, _ gen: Int) async {
    do {
      // The first successful send is our "connected" signal.
      try await task.send(.string(HubOutgoing.hello(controlCode: controlCode)))
      guard gen == generation else { return }
      // Before `.connected`, so no frame can overtake it: the hub labels frames
      // from a connection without a frame_source as "unknown".
      if let lastPhoneStatus {
        try await task.send(.string(lastPhoneStatus))
        guard gen == generation else { return }
      }
      state = .connected
      await receiveLoop(task, gen)
    } catch {
      fail(error, gen)
    }
  }

  private func receiveLoop(_ task: URLSessionWebSocketTask, _ gen: Int) async {
    while gen == generation {
      do {
        let message = try await task.receive()
        guard gen == generation else { return }
        if case .string(let text) = message, let incoming = HubIncoming.parse(text) {
          switch incoming {
          case .status(let status): continuation.yield(.status(status))
          case .ack(let ack): continuation.yield(.ack(ack))
          }
        }
      } catch {
        fail(error, gen)
        return
      }
    }
  }

  private func fail(_ error: Error, _ gen: Int) {
    guard gen == generation, url != nil else { return }
    generation += 1
    let next = generation
    task?.cancel(with: .abnormalClosure, reason: nil)
    task = nil
    state = .failed(error.localizedDescription)
    let delay = retryDelay
    Task {
      try? await Task.sleep(for: delay)
      await self.open(next)
    }
  }
}
