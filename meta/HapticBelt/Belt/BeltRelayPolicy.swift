/// The hub computes risk; the phone forwards the five validated sector levels.
/// A separate off heartbeat keeps a deliberately disabled, connected belt quiet.
struct BeltRelayPolicy {
  struct Output {
    let command: BeltCommand
    let label: String
  }
  static func evaluate(enabled: Bool, active: Bool, connected: Bool, streaming: Bool,
                       status: HubStatus?, receivedAt: Double?, now: Double) -> Output {
    guard enabled, active else { return Output(command: .off, label: "Belt alerts off") }
    let assessment = AlertAssessment.evaluate(connected: connected, streaming: streaming,
      status: status, receivedAt: receivedAt, now: now)
    if assessment.kind == .fault { return Output(command: .fault, label: assessment.label) }
    guard status?.belt == "simulated" else {
      return Output(command: .fault, label: "Use --belt sim on the hub when the iPhone drives the belt.")
    }
    switch assessment.kind {
    case .fault: return Output(command: .fault, label: assessment.label)
    case .quiet: return Output(command: .off, label: assessment.label)
    case .obstacle:
      guard let levels = status?.levels, let command = BeltCommand.levels(levels) else {
        return Output(command: .fault, label: "Invalid motor levels")
      }
      return Output(command: command, label: assessment.label)
    }
  }
}

/// One outstanding ATT write. No backlog; the caller supplies the latest intent
/// at each tick. Delivery health uses the 250 ms motor TTL; only an outstanding
/// ATT request stuck for one second tears down the BLE connection.
struct BeltWriteGate {
  struct Write: Equatable {
    let seq: UInt8
    let command: BeltCommand
    let at: Double
  }
  private(set) var pending: Write?
  private(set) var history: [Write] = []
  private(set) var acknowledged = 0
  private(set) var healthyWrites = 0
  private(set) var lastAckMs: Double?
  private(set) var maxCommandGapMs: Double?
  private var seq: UInt8 = 0
  private var nextAt = 0.0

  mutating func take(_ command: BeltCommand, now: Double) -> Write? {
    guard pending == nil, now >= nextAt, !timedOut(now: now) else { return nil }
    if let last = history.last {
      maxCommandGapMs = max(maxCommandGapMs ?? 0, (now - last.at) * 1000)
    }
    let write = Write(seq: seq, command: command, at: now)
    seq &+= 1
    pending = write
    // Match the hub's 15 Hz command budget. The 25 ms worker tick typically
    // sends every 75 ms, leaving more room for radio retries within 250 ms.
    nextAt = now + 1.0 / 15.0
    history.removeAll { now - $0.at > 0.5 }
    history.append(write)
    return write
  }
  mutating func ack(now: Double) {
    guard let pending else { return }
    let elapsed = now - pending.at
    lastAckMs = elapsed * 1000
    healthyWrites = elapsed >= 0 && elapsed < 0.25 ? healthyWrites + 1 : 0
    self.pending = nil
    acknowledged += 1
  }
  mutating func markUnreliable() { healthyWrites = 0 }
  func timedOut(now: Double) -> Bool {
    pending.map { now - $0.at >= 1 } ?? false
  }
  func deliveryOverdue(now: Double) -> Bool {
    (pending ?? history.last).map { now - $0.at >= 0.25 } ?? false
  }
  func matches(_ status: BeltFirmwareStatus, now: Double) -> Bool {
    guard status.commandAgeMs < BeltProtocol.ttlMs,
      let write = history.last(where: { $0.seq == status.seq && now >= $0.at && now - $0.at <= 0.5 }) else { return false }
    switch write.command.mode {
    case .off: return status.mode == 1 && status.levels.allSatisfy { $0 == 0 }
    case .levels: return status.mode == 2 && status.levels == write.command.levels
    case .fault: return status.mode == 3
    }
  }
}

/// A watchdog lease is necessary even with a live BLE link: a stopped producer
/// must not leave the radio heartbeat indefinitely replaying old motor levels.
struct BeltIntent {
  let command: BeltCommand
  let expiresAt: Double?
  let expired: BeltCommand
  static let off = Self(command: .off, expiresAt: nil, expired: .off)
  func current(now: Double) -> BeltCommand {
    guard let expiresAt else { return command }
    return now < expiresAt ? command : expired
  }
}

struct BeltStatusMonitor {
  let startedAt: Double
  private(set) var confirmed = false
  private var lastAt: Double?
  private var lastMatch: Double?
  private var mismatch = false

  mutating func receive(_ status: BeltFirmwareStatus, gate: BeltWriteGate, now: Double) {
    lastAt = now
    if gate.matches(status, now: now) {
      confirmed = true
      lastMatch = now
      mismatch = false
    } else if confirmed || now - startedAt > 1 {
      mismatch = true
    }
  }
  func fault(now: Double) -> String? {
    if !confirmed && now - startedAt <= 1 { return nil }
    guard let lastAt, now - lastAt <= 1 else { return "Belt status is stale" }
    if mismatch { return "Belt is not applying current commands" }
    guard let lastMatch, now - lastMatch <= 1 else { return "Belt has not confirmed current commands" }
    return nil
  }
  func hasMatched(since time: Double) -> Bool { lastMatch.map { $0 >= time } ?? false }
  func stalled(now: Double) -> Bool { now - (lastAt ?? startedAt) > 3 }
}
