import Foundation

/// Bounds sustained forwarding without rejecting every other camera frame when
/// callbacks arrive slightly early. Two credits permit one extra frame of jitter;
/// idle time never builds an unbounded burst. The forwarder separately permits
/// only one encode/send in flight and never queues images.
struct FrameRateLimiter: Sendable {
  private let framesPerSecond: Double
  private var credits = 2.0
  private var previous: ContinuousClock.Instant?

  init(framesPerSecond: Double) {
    precondition(framesPerSecond.isFinite && framesPerSecond > 0)
    self.framesPerSecond = framesPerSecond
  }

  mutating func allow(at now: ContinuousClock.Instant) -> Bool {
    if let previous {
      let duration = previous.duration(to: now).components
      let seconds = max(0, Double(duration.seconds) + Double(duration.attoseconds) / 1e18)
      credits = min(2, credits + seconds * framesPerSecond)
    }
    previous = now
    guard credits >= 1 else { return false }
    credits -= 1
    return true
  }
}
