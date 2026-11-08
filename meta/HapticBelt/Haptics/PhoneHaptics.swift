import CoreHaptics
import Observation

/// Finite haptic patterns only; no looping player and no queue of future alerts.
@Observable
@MainActor
final class PhoneHaptics {
  let supported = CHHapticEngine.capabilitiesForHardware().supportsHaptics
  private(set) var error: String?
  @ObservationIgnored private var engine: CHHapticEngine?
  @ObservationIgnored private var player: (any CHHapticPatternPlayer)?

  func play(_ cue: PhoneHapticPolicy.Cue, strength: Double = 0.7) {
    guard supported else {
      error = "This device does not support phone haptics."
      return
    }
    do {
      if engine == nil {
        let created = try CHHapticEngine()
        created.playsHapticsOnly = true
        created.isAutoShutdownEnabled = true
        created.resetHandler = { [weak self] in
          Task { @MainActor in self?.player = nil }
        }
        engine = created
      }
      guard let engine else { return }
      // Restart on demand after interruptions or idle shutdown; create a fresh
      // player each time so an engine reset cannot reuse an invalid player.
      try engine.start()
      try? player?.stop(atTime: CHHapticTimeImmediate)
      let events = cue.pulses(strength: strength).map { pulse in
        CHHapticEvent(eventType: pulse.duration == 0 ? .hapticTransient : .hapticContinuous,
                     parameters: [
          CHHapticEventParameter(parameterID: .hapticIntensity, value: pulse.intensity),
          CHHapticEventParameter(parameterID: .hapticSharpness, value: pulse.sharpness),
        ], relativeTime: pulse.start, duration: pulse.duration)
      }
      let pattern = try CHHapticPattern(events: events, parameters: [])
      let next = try engine.makePlayer(with: pattern)
      player = next
      try next.start(atTime: CHHapticTimeImmediate)
      error = nil
    } catch {
      self.error = "Phone vibration unavailable: \(error.localizedDescription)"
      player = nil
      engine = nil
    }
  }

  func cancelPattern() {
    try? player?.stop(atTime: CHHapticTimeImmediate)
    player = nil
  }

  func stop() {
    cancelPattern()
    engine?.stop(completionHandler: nil)
  }
}
