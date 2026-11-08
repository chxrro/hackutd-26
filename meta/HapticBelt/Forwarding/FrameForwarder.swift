//
// FrameForwarder.swift
//
// The bridge between the glasses and the hub. Accepts frames from any thread,
// keeps at most one in flight (newest wins, older frames are dropped rather than
// queued, since a queue would only add latency), JPEG-encodes and sends it.
//

import UIKit
import os

final class FrameForwarder: Sendable {
  struct Stats: Equatable, Sendable {
    var received = 0
    var sent = 0
    var dropped = 0
    var failed = 0
    var throttled = 0
    var busy = 0
    var completed = 0
    var encodeTotalMs = 0.0
    var sendTotalMs = 0.0
  }

  private struct State: Sendable {
    var inFlight = false
    var seq: UInt32 = 0
    var rateLimiter: FrameRateLimiter
    var stats = Stats()
  }

  private let state: OSAllocatedUnfairLock<State>
  private let hub: HubClient
  private let jpegQuality: CGFloat

  init(hub: HubClient, maxFps: Double = 15, jpegQuality: CGFloat = 0.6) {
    self.hub = hub
    self.state = OSAllocatedUnfairLock(initialState: State(rateLimiter: FrameRateLimiter(framesPerSecond: maxFps)))
    self.jpegQuality = jpegQuality
  }

  var stats: Stats { state.withLock { $0.stats } }

  /// Called on the SDK's frame thread. Returns immediately.
  func submit(_ image: UIImage) {
    let now = ContinuousClock.now
    let seq: UInt32? = state.withLock { s in
      s.stats.received += 1
      if s.inFlight {
        s.stats.busy += 1
        s.stats.dropped += 1
        return nil
      }
      if !s.rateLimiter.allow(at: now) {
        s.stats.throttled += 1
        s.stats.dropped += 1
        return nil
      }
      s.inFlight = true
      s.seq &+= 1
      return s.seq
    }
    guard let seq else { return }
    let captureMs = UInt64(Date().timeIntervalSince1970 * 1000)
    Task.detached(priority: .userInitiated) { [self] in
      await self.encodeAndSend(image, seq: seq, captureMs: captureMs)
    }
  }

  private func encodeAndSend(_ image: UIImage, seq: UInt32, captureMs: UInt64) async {
    var ok = false
    let encodeStart = ContinuousClock.now
    var sendStart: ContinuousClock.Instant?
    if let jpeg = image.jpegData(compressionQuality: jpegQuality) {
      let packet = FramePacket.encode(
        seq: seq,
        captureMs: captureMs,
        width: UInt16(clamping: Int(image.size.width * image.scale)),
        height: UInt16(clamping: Int(image.size.height * image.scale)),
        jpeg: jpeg
      )
      sendStart = .now
      ok = await hub.send(packet)
    }
    let finished = ContinuousClock.now
    let encodeMs = Self.milliseconds(encodeStart.duration(to: sendStart ?? finished))
    let sendMs = sendStart.map { Self.milliseconds($0.duration(to: finished)) } ?? 0
    let sent = ok
    state.withLock { s in
      s.inFlight = false
      s.stats.completed += 1
      s.stats.encodeTotalMs += encodeMs
      s.stats.sendTotalMs += sendMs
      if sent { s.stats.sent += 1 } else { s.stats.failed += 1 }
    }
  }

  private static func milliseconds(_ duration: Duration) -> Double {
    let c = duration.components
    return Double(c.seconds) * 1000 + Double(c.attoseconds) / 1e15
  }
}
