//
// HubClient.swift
//
// WebSocket connection to the hub laptop. Reconnects automatically while a URL
// is set. Knows nothing about the glasses.
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
  }

  /// Connection changes and hub status messages, for the UI.
  nonisolated let events: AsyncStream<Event>

  private let continuation: AsyncStream<Event>.Continuation
  private let session: URLSession
  private let retryDelay: Duration
  private var task: URLSessionWebSocketTask?
  private var url: URL?
  /// Bumped whenever the socket is replaced, so callbacks from an old socket are ignored.
  private var generation = 0
  private var lastPhoneStatus: String?
  private(set) var state: State = .disconnected {
    didSet { if state != oldValue { continuation.yield(.state(state)) } }
  }

  init(retryDelay: Duration = .seconds(1)) {
    (events, continuation) = AsyncStream.makeStream(of: Event.self, bufferingPolicy: .bufferingNewest(32))
    let config = URLSessionConfiguration.default
    config.timeoutIntervalForRequest = 5
    session = URLSession(configuration: config)
    self.retryDelay = retryDelay
  }

  func connect(to url: URL) {
    self.url = url
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

  /// Tells the hub what the glasses stream is doing. Re-sent after every reconnect.
  func reportPhoneStatus(stream: String) async {
    lastPhoneStatus = stream
    await sendText(HubOutgoing.phoneStatus(stream: stream))
  }

  // MARK: Private

  private func sendText(_ text: String) async {
    guard state == .connected, let task else { return }
    let gen = generation
    do {
      try await task.send(.string(text))
    } catch {
      fail(error, gen)
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
      try await task.send(.string(HubOutgoing.hello()))
      guard gen == generation else { return }
      state = .connected
      if let lastPhoneStatus {
        await sendText(HubOutgoing.phoneStatus(stream: lastPhoneStatus))
      }
      await receiveLoop(task, gen)
    } catch {
      fail(error, gen)
    }
  }

  private func receiveLoop(_ task: URLSessionWebSocketTask, _ gen: Int) async {
    while gen == generation {
      do {
        let message = try await task.receive()
        if case .string(let text) = message, let status = HubStatus.parse(text) {
          continuation.yield(.status(status))
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
