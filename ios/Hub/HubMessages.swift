//
// HubMessages.swift
//
// JSON text messages exchanged with the hub. Contract: docs/protocol.md
// sections 1-2 and "Control" (final version from the hub-dashboard branch). Based on
// meta/HapticBelt/Hub/HubMessages.swift, extended with control/ack.
//
// Unknown fields are ignored. The draft control fields (paused, settings,
// sectors) are decoded leniently: if one is malformed it is dropped and the
// rest of the status still counts, so a new hub can't break an old app.
//

import Foundation

enum Classifier: String, Codable, CaseIterable, Sendable {
  case local, nim
}

enum AlertRange: String, Codable, CaseIterable, Sendable {
  case near, normal, far
}

/// The hub's full settings, as sent in `ack.settings` and `status.settings`.
/// Values the app doesn't recognize are kept as raw strings.
struct HubSettings: Decodable, Equatable, Sendable {
  var classifier: String?
  var alertRange: String?
  var record: Bool?
  var paused: Bool?

  enum CodingKeys: String, CodingKey {
    case classifier, record, paused
    case alertRange = "alert_range"
  }

  init(classifier: String? = nil, alertRange: String? = nil, record: Bool? = nil, paused: Bool? = nil) {
    self.classifier = classifier
    self.alertRange = alertRange
    self.record = record
    self.paused = paused
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    classifier = (try? c.decodeIfPresent(String.self, forKey: .classifier)) ?? nil
    alertRange = (try? c.decodeIfPresent(String.self, forKey: .alertRange)) ?? nil
    record = (try? c.decodeIfPresent(Bool.self, forKey: .record)) ?? nil
    paused = (try? c.decodeIfPresent(Bool.self, forKey: .paused)) ?? nil
  }
}

/// What the hub is telling the belt to do, sent ~5x per second.
struct HubStatus: Decodable, Equatable, Sendable {
  struct Sector: Decodable, Equatable, Sendable {
    /// "person", "object" or nil: the kind of the largest labelled box in that
    /// motor's direction. For speech and display only. Says nothing about
    /// distance or safety; nil during faults, and nil doesn't mean "nothing there".
    let kind: String?

    init(kind: String?) { self.kind = kind }

    init(from decoder: Decoder) throws {
      let c = try decoder.container(keyedBy: CodingKeys.self)
      kind = (try? c.decodeIfPresent(String.self, forKey: .kind)) ?? nil
    }

    enum CodingKeys: String, CodingKey { case kind }
  }

  let mode: String  // "off" | "levels" | "fault"
  let healthy: Bool
  let reason: String?
  let levels: [Int]
  let belt: String  // "connected" | "connecting" | "disconnected" | "simulated"
  let source: String?
  let inferenceMs: Double?
  let frameAgeMs: Double?
  // Control draft additions (optional).
  let paused: Bool?
  let settings: HubSettings?
  let sectors: [Sector]?

  enum CodingKeys: String, CodingKey {
    case type, mode, healthy, reason, levels, belt, source, paused, settings, sectors
    case inferenceMs = "inference_ms"
    case frameAgeMs = "frame_age_ms"
  }

  init(
    mode: String = "levels", healthy: Bool = true, reason: String? = nil, levels: [Int] = [0, 0, 0, 0, 0],
    belt: String = "connected", source: String? = nil, inferenceMs: Double? = nil, frameAgeMs: Double? = nil,
    paused: Bool? = nil, settings: HubSettings? = nil, sectors: [Sector]? = nil
  ) {
    self.mode = mode
    self.healthy = healthy
    self.reason = reason
    self.levels = levels
    self.belt = belt
    self.source = source
    self.inferenceMs = inferenceMs
    self.frameAgeMs = frameAgeMs
    self.paused = paused
    self.settings = settings
    self.sectors = sectors
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    guard try c.decode(String.self, forKey: .type) == "status" else {
      throw DecodingError.dataCorruptedError(forKey: .type, in: c, debugDescription: "not a status message")
    }
    mode = try c.decode(String.self, forKey: .mode)
    healthy = try c.decode(Bool.self, forKey: .healthy)
    reason = try c.decodeIfPresent(String.self, forKey: .reason)
    levels = try c.decode([Int].self, forKey: .levels)
    belt = try c.decode(String.self, forKey: .belt)
    source = try c.decodeIfPresent(String.self, forKey: .source)
    inferenceMs = try c.decodeIfPresent(Double.self, forKey: .inferenceMs)
    frameAgeMs = try c.decodeIfPresent(Double.self, forKey: .frameAgeMs)
    paused = (try? c.decodeIfPresent(Bool.self, forKey: .paused)) ?? nil
    settings = (try? c.decodeIfPresent(HubSettings.self, forKey: .settings)) ?? nil
    sectors = (try? c.decodeIfPresent([Sector].self, forKey: .sectors)) ?? nil
  }

  /// Whether alerts are paused. Read from `paused` (or `settings.paused`), never
  /// from `reason`: a real fault takes priority over "alerts paused" there.
  var isPaused: Bool {
    paused ?? settings?.paused ?? false
  }

  /// The hub is playing a belt test sweep: those vibrations are not obstacles.
  var isBeltTest: Bool {
    reason == "belt test"
  }

  /// Returns nil for anything that isn't a well-formed status message.
  static func parse(_ text: String) -> HubStatus? {
    try? JSONDecoder().decode(HubStatus.self, from: Data(text.utf8))
  }
}

/// The hub's reply to one control request (sent to that client only).
struct HubAck: Decodable, Equatable, Sendable {
  /// Echoed as sent; nil if the hub couldn't read one.
  let id: Int?
  let ok: Bool
  let error: String?
  let settings: HubSettings?

  enum CodingKeys: String, CodingKey { case type, id, ok, error, settings }

  /// Only this exact error is meaningful to the app; show any other to the user.
  static let notPaired = "not paired"

  init(id: Int?, ok: Bool, error: String? = nil, settings: HubSettings? = nil) {
    self.id = id
    self.ok = ok
    self.error = error
    self.settings = settings
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    guard try c.decode(String.self, forKey: .type) == "ack" else {
      throw DecodingError.dataCorruptedError(forKey: .type, in: c, debugDescription: "not an ack message")
    }
    id = (try? c.decodeIfPresent(Int.self, forKey: .id)) ?? nil
    ok = try c.decode(Bool.self, forKey: .ok)
    error = try c.decodeIfPresent(String.self, forKey: .error)
    settings = (try? c.decodeIfPresent(HubSettings.self, forKey: .settings)) ?? nil
  }
}

/// Any text message from the hub the app understands. Other types are ignored.
enum HubIncoming: Equatable, Sendable {
  case status(HubStatus)
  case ack(HubAck)

  static func parse(_ text: String) -> HubIncoming? {
    struct Envelope: Decodable { let type: String }
    let data = Data(text.utf8)
    guard let type = try? JSONDecoder().decode(Envelope.self, from: data).type else { return nil }
    switch type {
    case "status": return (try? JSONDecoder().decode(HubStatus.self, from: data)).map(HubIncoming.status)
    case "ack": return (try? JSONDecoder().decode(HubAck.self, from: data)).map(HubIncoming.ack)
    default: return nil
    }
  }
}

/// A settings change. Only the non-nil keys are sent.
struct SettingsChange: Encodable, Equatable, Sendable {
  var classifier: Classifier?
  var alertRange: AlertRange?
  var record: Bool?

  enum CodingKeys: String, CodingKey {
    case classifier, record
    case alertRange = "alert_range"
  }
}

enum ControlAction: Equatable, Sendable {
  /// No effect: replies with the current settings, so it also checks pairing.
  case get
  case pause
  case resume
  case beltTest
  case set(SettingsChange)

  var wireName: String {
    switch self {
    case .get: return "get"
    case .pause: return "pause"
    case .resume: return "resume"
    case .beltTest: return "belt_test"
    case .set: return "set"
    }
  }
}

enum HubOutgoing {
  private struct Hello: Encodable {
    let type = "hello"
    let client = "ios"
    let version = 1
    let controlCode: String?

    enum CodingKeys: String, CodingKey {
      case type, client, version
      case controlCode = "control_code"
    }
  }

  private struct PhoneStatus: Encodable {
    let type = "phone_status"
    let stream: String
  }

  private struct Control: Encodable {
    let type = "control"
    let id: Int
    let action: String
    let settings: SettingsChange?
  }

  /// `control_code` is omitted when nil: the hub then accepts frames but refuses control.
  static func hello(controlCode: String? = nil) -> String { json(Hello(controlCode: controlCode)) }
  static func phoneStatus(stream: String) -> String { json(PhoneStatus(stream: stream)) }

  static func control(id: Int, _ action: ControlAction) -> String {
    var settings: SettingsChange?
    if case .set(let change) = action { settings = change }
    return json(Control(id: id, action: action.wireName, settings: settings))
  }

  private static func json(_ value: some Encodable) -> String {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    // Encoding these fixed structs can't fail.
    return String(decoding: (try? encoder.encode(value)) ?? Data("{}".utf8), as: UTF8.self)
  }
}
