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
    var sent = 0
    var dropped = 0
    var failed = 0
  }

  private struct State: Sendable {
    var inFlight = false
    var seq: UInt32 = 0
    var lastSentAt: ContinuousClock.Instant?
    var stats = Stats()
  }

  private let state = OSAllocatedUnfairLock(initialState: State())
  private let hub: HubClient
  private let minInterval: Duration
  private let jpegQuality: CGFloat

  init(hub: HubClient, maxFps: Double = 15, jpegQuality: CGFloat = 0.6) {
    self.hub = hub
    self.minInterval = .milliseconds(Int(1000 / maxFps))
    self.jpegQuality = jpegQuality
  }

  var stats: Stats { state.withLock { $0.stats } }

  /// Called on the SDK's frame thread. Returns immediately.
  func submit(_ image: UIImage) {
    let now = ContinuousClock.now
    let minInterval = self.minInterval
    let seq: UInt32? = state.withLock { s in
      if s.inFlight || s.lastSentAt.map({ now - $0 < minInterval }) == true {
        s.stats.dropped += 1
        return nil
      }
      s.inFlight = true
      s.lastSentAt = now
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
    if let jpeg = image.jpegData(compressionQuality: jpegQuality) {
      let packet = FramePacket.encode(
        seq: seq,
        captureMs: captureMs,
        width: UInt16(clamping: Int(image.size.width * image.scale)),
        height: UInt16(clamping: Int(image.size.height * image.scale)),
        jpeg: jpeg
      )
      ok = await hub.send(packet)
    }
    let sent = ok
    state.withLock { s in
      s.inFlight = false
      if sent { s.stats.sent += 1 } else { s.stats.failed += 1 }
    }
  }
}
