import Foundation

/// One interpretation for phone haptics and speech. Pure, SDK-free and testable.
struct AlertAssessment: Equatable {
  enum Kind { case quiet, obstacle, fault }
  let kind: Kind
  let level: Int
  let label: String
  let speech: String
  let briefSpeech: String
  let key: String

  static func fault(_ reason: String) -> Self {
    Self(kind: .fault, level: 0, label: "Not reliable: \(reason)",
         speech: reason, briefSpeech: reason, key: "fault")
  }

  static func evaluate(connected: Bool, streaming: Bool, status: HubStatus?,
                       receivedAt: Double?, now: Double) -> Self {
    guard connected else { return fault("Hub disconnected. Alerts unavailable.") }
    guard streaming else { return fault("Camera unavailable.") }
    guard let status, let receivedAt, now >= receivedAt, now - receivedAt <= 1 else {
      return fault("No fresh hub status. Alerts unavailable.")
    }
    // Don't speak arbitrary server error strings. Detailed reason stays on screen.
    guard status.healthy, status.mode != "fault" else {
      return fault("Alerts unavailable.")
    }
    let elapsedMs = (now - receivedAt) * 1000
    guard let age = status.frameAgeMs, age.isFinite, age >= 0, age + elapsedMs <= 1000 else {
      return fault("Camera frames are stale.")
    }
    guard ["off", "levels"].contains(status.mode), status.levels.count == 5,
      status.levels.allSatisfy({ (0...255).contains($0) }), !status.hazardsInvalid else {
      return fault("Invalid alert data.")
    }
    var events: [HazardEvent] = []
    if let hazards = status.hazards {
      guard hazards.version == 1, let seq = hazards.frameSeq, seq >= 0,
        let age = hazards.resultAgeMs, age.isFinite, age >= 0, age + elapsedMs <= 1000,
        hazards.events.count <= 5 else { return fault("Hazard information is stale or unavailable.") }
      events = hazards.events
      guard Set(events.map(\.sector)).count == events.count,
        events.allSatisfy({ $0.valid && status.levels[$0.sector] == $0.level }),
        events.count == (status.mode == "levels" ? status.levels.filter({ $0 > 0 }).count : 0)
      else { return fault("Invalid hazard information.") }
    }
    let level = status.mode == "levels" ? (status.levels.max() ?? 0) : 0
    guard level > 0 else {
      return Self(kind: .quiet, level: 0, label: "No current alert — not proof of a clear path",
                  speech: "", briefSpeech: "", key: "quiet")
    }
    let names = ["left", "mid-left", "center", "mid-right", "right"]
    let directions = status.levels.enumerated().filter { $0.element > 0 }.map { names[$0.offset] }
    // Strongest hazard; prefer center on ties. Stable across frame sequence changes.
    let primary = events.sorted {
      if $0.level != $1.level { return $0.level > $1.level }
      if abs($0.sector - 2) != abs($1.sector - 2) { return abs($0.sector - 2) < abs($1.sector - 2) }
      return $0.sector < $1.sector
    }.first
    let sector = primary?.sector ?? status.levels.enumerated().max(by: { $0.element < $1.element })!.offset
    let direction = ["to the left", "slightly left", "ahead", "slightly right", "to the right"][sector]
    let knownLabels: [String: String] = ["person": "Person", "chair": "Chair", "table": "Table",
      "dining table": "Table", "bicycle": "Bicycle", "car": "Car"]
    let noun = primary?.label.flatMap { knownLabels[$0] } ?? "Obstacle"
    let motion = primary?.motion ?? "unknown"
    let description = motion == "approaching" ? "getting closer" : (motion == "crossing" ? "crossing" : "")
    let phrase = description.isEmpty ? "\(noun) \(direction)." : "\(noun) \(description), \(direction)."
    return Self(kind: .obstacle, level: level, label: "Obstacle alert: " + directions.joined(separator: ", "),
                speech: phrase, briefSpeech: "Obstacle \(direction).",
                key: "\(sector):\(noun):\(motion)")
  }
}

struct HazardSnapshot: Decodable, Equatable, Sendable {
  let version: Int
  let frameSeq: Int?
  let resultAgeMs: Double?
  let events: [HazardEvent]
  enum CodingKeys: String, CodingKey {
    case version, events
    case frameSeq = "frame_seq", resultAgeMs = "result_age_ms"
  }
}

struct HazardEvent: Decodable, Equatable, Sendable {
  let id: String
  let sector: Int
  let level: Int
  let evidence: String
  let motion: String
  let label: String?
  var valid: Bool {
    guard (0..<5).contains(sector), (1...255).contains(level), !id.isEmpty, id.count <= 100,
      label == nil || label!.count <= 40 else { return false }
    switch evidence {
    case "depth", "unknown": return motion == "unknown" && label == nil
    case "box_ttc", "flow_approach": return motion == "approaching"
    case "flow_crossing": return motion == "crossing"
    default: return false
    }
  }
}
