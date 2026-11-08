/// Shared urgency interpretation. Levels are hub command strengths, not distances
/// or calibrated collision probabilities. Hysteresis avoids threshold chatter.
enum AlertUrgency: Equatable {
  case notice, urgent

  static func resolve(level: Int, previous: Self) -> Self {
    if level >= 220 { return .urgent }
    if previous == .urgent && level >= 200 { return .urgent }
    return .notice
  }
}

enum AlertPatternSample: String, CaseIterable, Identifiable {
  case obstacle, urgent, unavailable
  var id: String { rawValue }
  var title: String {
    switch self {
    case .obstacle: "Obstacle · single buzz"
    case .urgent: "Higher urgency · double buzz"
    case .unavailable: "Unavailable · three taps"
    }
  }
  var cue: PhoneHapticPolicy.Cue {
    switch self {
    case .obstacle: .obstacle(level: 170)
    case .urgent: .urgent(level: 240)
    case .unavailable: .fault
    }
  }
  var explanation: String {
    switch self {
    case .obstacle: "Pattern test. One buzz means an obstacle alert. Voice describes its direction."
    case .urgent: "Pattern test. Two buzzes mean higher urgency. Voice says urgent, then the obstacle direction."
    case .unavailable: "Pattern test. Three taps mean alerts are unavailable. This is not an obstacle warning."
    }
  }
}

/// SDK-free pattern specification, so bounds and distinct rhythms can be tested.
struct PhonePulse: Equatable {
  let start: Double
  let duration: Double // zero = transient tap
  let intensity: Float
  let sharpness: Float
}

extension PhoneHapticPolicy.Cue {
  func pulses(strength: Double) -> [PhonePulse] {
    let gain = Float(strength.isFinite ? min(1, max(0.2, strength)) : 0.7)
    switch self {
    case .obstacle(let level):
      let amplitude = Float(0.4 + 0.4 * Double(min(255, max(0, level))) / 255) * gain
      return [PhonePulse(start: 0, duration: 0.12, intensity: amplitude, sharpness: 0.4)]
    case .urgent:
      return [0.0, 0.20].map {
        PhonePulse(start: $0, duration: 0.12, intensity: gain, sharpness: 0.8)
      }
    case .fault:
      return [0.0, 0.16, 0.32].map {
        PhonePulse(start: $0, duration: 0, intensity: 0.85 * gain, sharpness: 1)
      }
    }
  }
}
