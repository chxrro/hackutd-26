import Foundation

/// Bench-only interpretation of existing hub status. One phone actuator cannot
/// reproduce the belt's spatial cues. Time is monotonic seconds, supplied by caller.
struct PhoneHapticPolicy {
  enum Cue: Equatable {
    case obstacle(level: Int)
    case urgent(level: Int)
    case fault
  }

  struct Output: Equatable {
    let label: String
    let cue: Cue?
    let cancel: Bool
  }

  private enum Kind { case quiet, obstacle, urgent, fault }
  private var lastKind: Kind = .quiet
  private var nextPulse = 0.0

  mutating func step(
    enabled: Bool, active: Bool, connected: Bool, streaming: Bool,
    status: HubStatus?, receivedAt: Double?, now: Double
  ) -> Output {
    let assessment = AlertAssessment.evaluate(connected: connected, streaming: streaming,
      status: status, receivedAt: receivedAt, now: now)
    return step(assessment: assessment, enabled: enabled, active: active, now: now)
  }

  mutating func step(assessment: AlertAssessment, enabled: Bool, active: Bool, now: Double) -> Output {
    guard enabled, active else {
      return output(.quiet, label: enabled ? "Paused while app is inactive" : "Phone vibrations off", level: 0, now: now)
    }
    switch assessment.kind {
    case .quiet: return output(.quiet, label: assessment.label, level: 0, now: now)
    case .fault: return output(.fault, label: assessment.label, level: 0, now: now)
    case .obstacle:
      let urgency = AlertUrgency.resolve(level: assessment.level, previous: lastKind == .urgent ? .urgent : .notice)
      return output(urgency == .urgent ? .urgent : .obstacle,
                    label: urgency == .urgent ? "Higher urgency: \(assessment.label)" : assessment.label,
                    level: assessment.level, now: now)
    }
  }

  private mutating func output(_ kind: Kind, label: String, level: Int, now: Double) -> Output {
    let changed = kind != lastKind
    if changed { nextPulse = now }
    lastKind = kind
    guard kind != .quiet, now >= nextPulse else {
      return Output(label: label, cue: nil, cancel: changed)
    }
    let cue: Cue
    switch kind {
    case .fault: cue = .fault; nextPulse = now + 3
    case .urgent: cue = .urgent(level: level); nextPulse = now + 0.6
    default: cue = .obstacle(level: level); nextPulse = now + 1.2
    }
    return Output(label: label, cue: cue, cancel: changed)
  }
}
